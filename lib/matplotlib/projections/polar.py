from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

import six

import math
import warnings

import numpy as np

import matplotlib
rcParams = matplotlib.rcParams
from matplotlib.axes import Axes
import matplotlib.axis as maxis
from matplotlib import cbook
from matplotlib import docstring
import matplotlib.patches as mpatches
import matplotlib.path as mpath
import matplotlib.ticker as mticker
import matplotlib.transforms as mtransforms
import matplotlib.spines as mspines


class PolarTransform(mtransforms.Transform):
    """
    The base polar transform.  This handles projection *theta* and
    *r* into Cartesian coordinate space *x* and *y*, but does not
    perform the ultimate affine transformation into the correct
    position.
    """
    input_dims = 2
    output_dims = 2
    is_separable = False

    def __init__(self, axis=None, use_rmin=True,
                 _apply_theta_transforms=True):
        mtransforms.Transform.__init__(self)
        self._axis = axis
        self._use_rmin = use_rmin
        self._apply_theta_transforms = _apply_theta_transforms

    def transform_non_affine(self, tr):
        xy = np.empty(tr.shape, float)

        t = tr[:, 0:1]
        r = tr[:, 1:2]
        x = xy[:, 0:1]
        y = xy[:, 1:2]

        # PolarAxes does not use the theta transforms here, but apply them for
        # backwards-compatibility if not being used by it.
        if self._apply_theta_transforms and self._axis is not None:
            t *= self._axis.get_theta_direction()
            t += self._axis.get_theta_offset()

        if self._use_rmin and self._axis is not None:
            r = r - self._axis.get_rorigin()
        mask = r < 0
        x[:] = np.where(mask, np.nan, r * np.cos(t))
        y[:] = np.where(mask, np.nan, r * np.sin(t))

        return xy
    transform_non_affine.__doc__ = \
        mtransforms.Transform.transform_non_affine.__doc__

    def transform_path_non_affine(self, path):
        vertices = path.vertices
        if len(vertices) == 2 and vertices[0, 0] == vertices[1, 0]:
            return mpath.Path(self.transform(vertices), path.codes)
        ipath = path.interpolated(path._interpolation_steps)
        return mpath.Path(self.transform(ipath.vertices), ipath.codes)
    transform_path_non_affine.__doc__ = \
        mtransforms.Transform.transform_path_non_affine.__doc__

    def inverted(self):
        return PolarAxes.InvertedPolarTransform(self._axis, self._use_rmin,
                                                self._apply_theta_transforms)
    inverted.__doc__ = mtransforms.Transform.inverted.__doc__


class PolarAffine(mtransforms.Affine2DBase):
    """
    The affine part of the polar projection.  Scales the output so
    that maximum radius rests on the edge of the axes circle.
    """
    def __init__(self, scale_transform, limits):
        """
        *limits* is the view limit of the data.  The only part of
        its bounds that is used is ymax (for the radius maximum).
        The theta range is always fixed to (0, 2pi).
        """
        mtransforms.Affine2DBase.__init__(self)
        self._scale_transform = scale_transform
        self._limits = limits
        self.set_children(scale_transform, limits)
        self._mtx = None

    def get_matrix(self):
        if self._invalid:
            limits_scaled = self._limits.transformed(self._scale_transform)
            yscale = limits_scaled.ymax - limits_scaled.ymin
            affine = mtransforms.Affine2D() \
                .scale(0.5 / yscale) \
                .translate(0.5, 0.5)
            self._mtx = affine.get_matrix()
            self._inverted = None
            self._invalid = 0
        return self._mtx
    get_matrix.__doc__ = mtransforms.Affine2DBase.get_matrix.__doc__


class InvertedPolarTransform(mtransforms.Transform):
    """
    The inverse of the polar transform, mapping Cartesian
    coordinate space *x* and *y* back to *theta* and *r*.
    """
    input_dims = 2
    output_dims = 2
    is_separable = False

    def __init__(self, axis=None, use_rmin=True,
                 _apply_theta_transforms=True):
        mtransforms.Transform.__init__(self)
        self._axis = axis
        self._use_rmin = use_rmin
        self._apply_theta_transforms = _apply_theta_transforms

    def transform_non_affine(self, xy):
        x = xy[:, 0:1]
        y = xy[:, 1:]
        r = np.sqrt(x*x + y*y)
        with np.errstate(invalid='ignore'):
            # At x=y=r=0 this will raise an
            # invalid value warning when doing 0/0
            # Divide by zero warnings are only raised when
            # the numerator is different from 0. That
            # should not happen here.
            theta = np.arccos(x / r)
        theta = np.where(y < 0, 2 * np.pi - theta, theta)

        # PolarAxes does not use the theta transforms here, but apply them for
        # backwards-compatibility if not being used by it.
        if self._apply_theta_transforms and self._axis is not None:
            theta -= self._axis.get_theta_offset()
            theta *= self._axis.get_theta_direction()
            theta %= 2 * np.pi

        if self._use_rmin and self._axis is not None:
            r += self._axis.get_rorigin()

        return np.concatenate((theta, r), 1)
    transform_non_affine.__doc__ = \
        mtransforms.Transform.transform_non_affine.__doc__

    def inverted(self):
        return PolarAxes.PolarTransform(self._axis, self._use_rmin,
                                        self._apply_theta_transforms)
    inverted.__doc__ = mtransforms.Transform.inverted.__doc__


class ThetaFormatter(mticker.Formatter):
    """
    Used to format the *theta* tick labels.  Converts the native
    unit of radians into degrees and adds a degree symbol.
    """
    def __call__(self, x, pos=None):
        vmin, vmax = self.axis.get_view_interval()
        d = np.rad2deg(abs(vmax - vmin))
        digits = max(-int(np.log10(d) - 1.5), 0)

        if rcParams['text.usetex'] and not rcParams['text.latex.unicode']:
            format_str = r"${value:0.{digits:d}f}^\circ$"
            return format_str.format(value=np.rad2deg(x), digits=digits)
        else:
            # we use unicode, rather than mathtext with \circ, so
            # that it will work correctly with any arbitrary font
            # (assuming it has a degree sign), whereas $5\circ$
            # will only work correctly with one of the supported
            # math fonts (Computer Modern and STIX)
            format_str = "{value:0.{digits:d}f}\N{DEGREE SIGN}"
            return format_str.format(value=np.rad2deg(x), digits=digits)


class RadialLocator(mticker.Locator):
    """
    Used to locate radius ticks.

    Ensures that all ticks are strictly positive.  For all other
    tasks, it delegates to the base
    :class:`~matplotlib.ticker.Locator` (which may be different
    depending on the scale of the *r*-axis.
    """
    def __init__(self, base, axes=None):
        self.base = base
        self._axes = axes

    def __call__(self):
        show_all = True
        # Ensure previous behaviour with full circle views.
        if self._axes:
            rorigin = self._axes.get_rorigin()
            if self._axes.get_rmin() <= rorigin:
                show_all = False

        if show_all:
            return self.base()
        else:
            return [tick for tick in self.base() if tick > rorigin]

    def autoscale(self):
        return self.base.autoscale()

    def pan(self, numsteps):
        return self.base.pan(numsteps)

    def zoom(self, direction):
        return self.base.zoom(direction)

    def refresh(self):
        return self.base.refresh()

    def view_limits(self, vmin, vmax):
        vmin, vmax = self.base.view_limits(vmin, vmax)
        return mtransforms.nonsingular(min(0, vmin), vmax)


class PolarAxes(Axes):
    """
    A polar graph projection, where the input dimensions are *theta*, *r*.

    Theta starts pointing east and goes anti-clockwise.
    """
    name = 'polar'

    def __init__(self, *args, **kwargs):
        """
        Create a new Polar Axes for a polar plot.
        """
        self._default_theta_offset = kwargs.pop('theta_offset', 0)
        self._default_theta_direction = kwargs.pop('theta_direction', 1)
        self._default_rlabel_position = np.deg2rad(
            kwargs.pop('rlabel_position', 22.5))

        Axes.__init__(self, *args, **kwargs)
        self.set_aspect('equal', adjustable='box', anchor='C')
        self.cla()
    __init__.__doc__ = Axes.__init__.__doc__

    def cla(self):
        Axes.cla(self)

        self.title.set_y(1.05)

        self.xaxis.set_major_formatter(self.ThetaFormatter())
        self.xaxis.isDefault_majfmt = True
        angles = np.arange(0.0, 360.0, 45.0)
        self.set_thetagrids(angles)
        self.yaxis.set_major_locator(
            self.RadialLocator(self.yaxis.get_major_locator(), self))

        self.grid(rcParams['polaraxes.grid'])
        self.xaxis.set_ticks_position('none')
        inner = self.spines.get('inner', None)
        if inner:
            inner.set_visible(False)
        self.yaxis.set_ticks_position('none')
        self.yaxis.set_tick_params(label1On=True)
        # Why do we need to turn on yaxis tick labels, but
        # xaxis tick labels are already on?

        self.set_rorigin(None)
        self.set_theta_offset(self._default_theta_offset)
        self.set_theta_direction(self._default_theta_direction)

    def _init_axis(self):
        "move this out of __init__ because non-separable axes don't use it"
        self.xaxis = maxis.XAxis(self)
        self.yaxis = maxis.YAxis(self)
        # Calling polar_axes.xaxis.cla() or polar_axes.xaxis.cla()
        # results in weird artifacts. Therefore we disable this for
        # now.
        # self.spines['polar'].register_axis(self.yaxis)
        self._update_transScale()

    def _set_lim_and_transforms(self):
        # A view limit where the minimum radius can be locked if the user
        # specifies an alternate origin.
        self._originViewLim = mtransforms.LockableBbox(self.viewLim)

        # Handle angular offset and direction.
        self._direction = mtransforms.Affine2D() \
            .scale(self._default_theta_direction, 1.0)
        self._theta_offset = mtransforms.Affine2D() \
            .translate(self._default_theta_offset, 0.0)
        self.transShift = mtransforms.composite_transform_factory(
            self._direction,
            self._theta_offset)
        # A view limit shifted to the correct location after accounting for
        # orientation and offset.
        self._shiftedViewLim = mtransforms.TransformedBbox(self.viewLim,
                                                           self.transShift)

        self.transAxes = mtransforms.BboxTransformTo(self.bbox)

        # Transforms the x and y axis separately by a scale factor
        # It is assumed that this part will have non-linear components
        self.transScale = mtransforms.TransformWrapper(
            mtransforms.IdentityTransform())

        # A (possibly non-linear) projection on the (already scaled)
        # data.  This one is aware of rmin
        self.transProjection = self.PolarTransform(
            self,
            _apply_theta_transforms=False)
        # Add dependency on rorigin.
        self.transProjection.set_children(self._originViewLim)

        # This one is not aware of rmin
        self.transPureProjection = self.PolarTransform(
            self,
            use_rmin=False,
            _apply_theta_transforms=False)

        # An affine transformation on the data, generally to limit the
        # range of the axes
        self.transProjectionAffine = self.PolarAffine(self.transScale,
                                                      self._originViewLim)

        # The complete data transformation stack -- from data all the
        # way to display coordinates
        self.transData = (
            self.transScale + self.transShift + self.transProjection +
            (self.transProjectionAffine + self.transAxes))

        # This is the transform for theta-axis ticks.  It is
        # equivalent to transData, except it always puts r == 1.0 at
        # the edge of the axis circle.
        self._xaxis_transform = (
            self.transShift +
            self.transPureProjection +
            self.PolarAffine(mtransforms.IdentityTransform(),
                             mtransforms.Bbox.unit()) +
            self.transAxes)
        # The theta labels are moved from radius == 0.0 to radius == 1.1
        self._theta_label1_position = (
            mtransforms.Affine2D().translate(0.0, 1.1))
        self._xaxis_text1_transform = (
            self._theta_label1_position +
            self._xaxis_transform)
        self._theta_label2_position = (
            mtransforms.Affine2D().translate(0.0, 1.0 / 1.1))
        self._xaxis_text2_transform = (
            self._theta_label2_position +
            self._xaxis_transform)

        # This is the transform for r-axis ticks.  It scales the theta
        # axis so the gridlines from 0.0 to 1.0, now go from 0.0 to
        # 2pi.
        self._yaxis_transform = (
            self.transShift +
            mtransforms.Affine2D().scale(np.pi * 2.0, 1.0) +
            self.transData)
        # The r-axis labels are put at an angle and padded in the r-direction
        self._r_label_position = mtransforms.ScaledTranslation(
            self._default_rlabel_position, 0.0, mtransforms.Affine2D())
        self._yaxis_text_transform = self._r_label_position + self.transData

    def get_xaxis_transform(self,which='grid'):
        if which not in ['tick1','tick2','grid']:
            msg = "'which' must be one of [ 'tick1' | 'tick2' | 'grid' ]"
            raise ValueError(msg)
        return self._xaxis_transform

    def get_xaxis_text1_transform(self, pad):
        return self._xaxis_text1_transform, 'center', 'center'

    def get_xaxis_text2_transform(self, pad):
        return self._xaxis_text2_transform, 'center', 'center'

    def get_yaxis_transform(self,which='grid'):
        if which not in ['tick1','tick2','grid']:
            msg = "'which' must be on of [ 'tick1' | 'tick2' | 'grid' ]"
            raise ValueError(msg)
        return self._yaxis_transform

    def get_yaxis_text1_transform(self, pad):
        angle = self.get_rlabel_position()
        if angle < 90.:
            return self._yaxis_text_transform, 'bottom', 'left'
        elif angle < 180.:
            return self._yaxis_text_transform, 'bottom', 'right'
        elif angle < 270.:
            return self._yaxis_text_transform, 'top', 'right'
        else:
            return self._yaxis_text_transform, 'top', 'left'

    def get_yaxis_text2_transform(self, pad):
        angle = self.get_rlabel_position()
        if angle < 90.:
            return self._yaxis_text_transform, 'top', 'right'
        elif angle < 180.:
            return self._yaxis_text_transform, 'top', 'left'
        elif angle < 270.:
            return self._yaxis_text_transform, 'bottom', 'left'
        else:
            return self._yaxis_text_transform, 'bottom', 'right'

    def draw(self, *args, **kwargs):
        rmin, rmax = self.viewLim.intervaly
        rorigin = self.get_rorigin()
        inner = self.spines.get('inner', None)

        if isinstance(self.patch, mpatches.Wedge):
            # Backwards-compatibility: Any subclassed Axes might override the
            # patch to not be the Wedge that PolarAxes uses.
            if rorigin < rmin:
                width = (rmax - rmin) / (rmax - rorigin) * 0.5
            else:
                width = 0.5
            self.patch.set_width(width)

            inner_width = 0.5 - width
            if inner:
                inner.set_patch_circle((0.5, 0.5), inner_width)
                inner.set_visible(inner_width != 0.0)

        Axes.draw(self, *args, **kwargs)

    def _gen_axes_patch(self):
        return mpatches.Wedge((0.5, 0.5), 0.5, 0.0, 360.0)

    def _gen_axes_spines(self):
        return {'polar': mspines.Spine.circular_spine(self,
                                                      (0.5, 0.5), 0.5),
                'inner': mspines.Spine.circular_spine(self,
                                                      (0.5, 0.5), 0.0)}

    def set_theta_offset(self, offset):
        """
        Set the offset for the location of 0 in radians.
        """
        mtx = self._theta_offset.get_matrix()
        mtx[0, 2] = offset
        self._theta_offset.invalidate()

    def get_theta_offset(self):
        """
        Get the offset for the location of 0 in radians.
        """
        return self._theta_offset.get_matrix()[0, 2]

    def set_theta_zero_location(self, loc):
        """
        Sets the location of theta's zero.  (Calls set_theta_offset
        with the correct value in radians under the hood.)

        May be one of "N", "NW", "W", "SW", "S", "SE", "E", or "NE".
        """
        mapping = {
            'N': np.pi * 0.5,
            'NW': np.pi * 0.75,
            'W': np.pi,
            'SW': np.pi * 1.25,
            'S': np.pi * 1.5,
            'SE': np.pi * 1.75,
            'E': 0,
            'NE': np.pi * 0.25 }
        return self.set_theta_offset(mapping[loc])

    def set_theta_direction(self, direction):
        """
        Set the direction in which theta increases.

        clockwise, -1:
           Theta increases in the clockwise direction

        counterclockwise, anticlockwise, 1:
           Theta increases in the counterclockwise direction
        """
        mtx = self._direction.get_matrix()
        if direction in ('clockwise',):
            mtx[0, 0] = -1
        elif direction in ('counterclockwise', 'anticlockwise'):
            mtx[0, 0] = 1
        elif direction in (1, -1):
            mtx[0, 0] = direction
        else:
            raise ValueError("direction must be 1, -1, clockwise or counterclockwise")
        self._direction.invalidate()

    def get_theta_direction(self):
        """
        Get the direction in which theta increases.

        -1:
           Theta increases in the clockwise direction

        1:
           Theta increases in the counterclockwise direction
        """
        return self._direction.get_matrix()[0, 0]

    def set_rmax(self, rmax):
        self.viewLim.y1 = rmax

    def get_rmax(self):
        return self.viewLim.ymax

    def set_rmin(self, rmin):
        self.viewLim.y0 = rmin

    def get_rmin(self):
        return self.viewLim.ymin

    def set_rorigin(self, rorigin):
        self._originViewLim.locked_y0 = rorigin

    def get_rorigin(self):
        return self._originViewLim.y0

    def set_rlim(self, *args, **kwargs):
        if 'rmin' in kwargs:
            kwargs['ymin'] = kwargs.pop('rmin')
        if 'rmax' in kwargs:
            kwargs['ymax'] = kwargs.pop('rmax')
        return self.set_ylim(*args, **kwargs)

    def get_rlabel_position(self):
        """
        Returns
        -------
        float
            The theta position of the radius labels in degrees.
        """
        return np.rad2deg(self._r_label_position.to_values()[4])

    def set_rlabel_position(self, value):
        """Updates the theta position of the radius labels.

        Parameters
        ----------
        value : number
            The angular position of the radius labels in degrees.
        """
        self._r_label_position._t = (np.deg2rad(value), 0.0)
        self._r_label_position.invalidate()

    def set_yscale(self, *args, **kwargs):
        Axes.set_yscale(self, *args, **kwargs)
        self.yaxis.set_major_locator(
            self.RadialLocator(self.yaxis.get_major_locator(), self))

    def set_rscale(self, *args, **kwargs):
        return Axes.set_yscale(self, *args, **kwargs)
    def set_rticks(self, *args, **kwargs):
        return Axes.set_yticks(self, *args, **kwargs)

    @docstring.dedent_interpd
    def set_thetagrids(self, angles, labels=None, frac=None, fmt=None,
                       **kwargs):
        """
        Set the angles at which to place the theta grids (these
        gridlines are equal along the theta dimension).  *angles* is in
        degrees.

        *labels*, if not None, is a ``len(angles)`` list of strings of
        the labels to use at each angle.

        If *labels* is None, the labels will be ``fmt %% angle``

        *frac* is the fraction of the polar axes radius at which to
        place the label (1 is the edge). e.g., 1.05 is outside the axes
        and 0.95 is inside the axes.

        Return value is a list of tuples (*line*, *label*), where
        *line* is :class:`~matplotlib.lines.Line2D` instances and the
        *label* is :class:`~matplotlib.text.Text` instances.

        kwargs are optional text properties for the labels:

        %(Text)s

        ACCEPTS: sequence of floats
        """
        # Make sure we take into account unitized data
        angles = self.convert_yunits(angles)
        angles = np.asarray(angles, float)
        self.set_xticks(angles * (np.pi / 180.0))
        if labels is not None:
            self.set_xticklabels(labels)
        elif fmt is not None:
            self.xaxis.set_major_formatter(mticker.FormatStrFormatter(fmt))
        if frac is not None:
            self._theta_label1_position.clear().translate(0.0, frac)
            self._theta_label2_position.clear().translate(0.0, 1.0 / frac)
        for t in self.xaxis.get_ticklabels():
            t.update(kwargs)
        return self.xaxis.get_ticklines(), self.xaxis.get_ticklabels()

    @docstring.dedent_interpd
    def set_rgrids(self, radii, labels=None, angle=None, fmt=None,
                   **kwargs):
        """
        Set the radial locations and labels of the *r* grids.

        The labels will appear at radial distances *radii* at the
        given *angle* in degrees.

        *labels*, if not None, is a ``len(radii)`` list of strings of the
        labels to use at each radius.

        If *labels* is None, the built-in formatter will be used.

        Return value is a list of tuples (*line*, *label*), where
        *line* is :class:`~matplotlib.lines.Line2D` instances and the
        *label* is :class:`~matplotlib.text.Text` instances.

        kwargs are optional text properties for the labels:

        %(Text)s

        ACCEPTS: sequence of floats
        """
        # Make sure we take into account unitized data
        radii = self.convert_xunits(radii)
        radii = np.asarray(radii)

        self.set_yticks(radii)
        if labels is not None:
            self.set_yticklabels(labels)
        elif fmt is not None:
            self.yaxis.set_major_formatter(mticker.FormatStrFormatter(fmt))
        if angle is None:
            angle = self.get_rlabel_position()
        self.set_rlabel_position(angle)
        for t in self.yaxis.get_ticklabels():
            t.update(kwargs)
        return self.yaxis.get_gridlines(), self.yaxis.get_ticklabels()

    def set_xscale(self, scale, *args, **kwargs):
        if scale != 'linear':
            raise NotImplementedError("You can not set the xscale on a polar plot.")

    def set_xlim(self, *args, **kargs):
        # The xlim is fixed, no matter what you do
        self.viewLim.intervalx = (0.0, np.pi * 2.0)

    def format_coord(self, theta, r):
        """
        Return a format string formatting the coordinate using Unicode
        characters.
        """
        if theta < 0:
            theta += 2 * math.pi
        theta /= math.pi
        return ('\N{GREEK SMALL LETTER THETA}=%0.3f\N{GREEK SMALL LETTER PI} '
                '(%0.3f\N{DEGREE SIGN}), r=%0.3f') % (theta, theta * 180.0, r)

    def get_data_ratio(self):
        '''
        Return the aspect ratio of the data itself.  For a polar plot,
        this should always be 1.0
        '''
        return 1.0

    ### Interactive panning

    def can_zoom(self):
        """
        Return *True* if this axes supports the zoom box button functionality.

        Polar axes do not support zoom boxes.
        """
        return False

    def can_pan(self) :
        """
        Return *True* if this axes supports the pan/zoom button functionality.

        For polar axes, this is slightly misleading. Both panning and
        zooming are performed by the same button. Panning is performed
        in azimuth while zooming is done along the radial.
        """
        return True

    def start_pan(self, x, y, button):
        angle = np.deg2rad(self.get_rlabel_position())
        mode = ''
        if button == 1:
            epsilon = np.pi / 45.0
            t, r = self.transData.inverted().transform_point((x, y))
            if t >= angle - epsilon and t <= angle + epsilon:
                mode = 'drag_r_labels'
        elif button == 3:
            mode = 'zoom'

        self._pan_start = cbook.Bunch(
            rmax          = self.get_rmax(),
            trans         = self.transData.frozen(),
            trans_inverse = self.transData.inverted().frozen(),
            r_label_angle = self.get_rlabel_position(),
            x             = x,
            y             = y,
            mode          = mode
            )

    def end_pan(self):
        del self._pan_start

    def drag_pan(self, button, key, x, y):
        p = self._pan_start

        if p.mode == 'drag_r_labels':
            startt, startr = p.trans_inverse.transform_point((p.x, p.y))
            t, r = p.trans_inverse.transform_point((x, y))

            # Deal with theta
            dt0 = t - startt
            dt1 = startt - t
            if abs(dt1) < abs(dt0):
                dt = abs(dt1) * np.sign(dt0) * -1.0
            else:
                dt = dt0 * -1.0
            dt = (dt / np.pi) * 180.0
            self.set_rlabel_position(p.r_label_angle - dt)

            trans, vert1, horiz1 = self.get_yaxis_text1_transform(0.0)
            trans, vert2, horiz2 = self.get_yaxis_text2_transform(0.0)
            for t in self.yaxis.majorTicks + self.yaxis.minorTicks:
                t.label1.set_va(vert1)
                t.label1.set_ha(horiz1)
                t.label2.set_va(vert2)
                t.label2.set_ha(horiz2)

        elif p.mode == 'zoom':
            startt, startr = p.trans_inverse.transform_point((p.x, p.y))
            t, r = p.trans_inverse.transform_point((x, y))

            dr = r - startr

            # Deal with r
            scale = r / startr
            self.set_rmax(p.rmax / scale)


# to keep things all self contained, we can put aliases to the Polar classes
# defined above. This isn't strictly necessary, but it makes some of the
# code more readable (and provides a backwards compatible Polar API)
PolarAxes.PolarTransform = PolarTransform
PolarAxes.PolarAffine = PolarAffine
PolarAxes.InvertedPolarTransform = InvertedPolarTransform
PolarAxes.ThetaFormatter = ThetaFormatter
PolarAxes.RadialLocator = RadialLocator


# These are a couple of aborted attempts to project a polar plot using
# cubic bezier curves.

#         def transform_path(self, path):
#             twopi = 2.0 * np.pi
#             halfpi = 0.5 * np.pi

#             vertices = path.vertices
#             t0 = vertices[0:-1, 0]
#             t1 = vertices[1:  , 0]
#             td = np.where(t1 > t0, t1 - t0, twopi - (t0 - t1))
#             maxtd = td.max()
#             interpolate = np.ceil(maxtd / halfpi)
#             if interpolate > 1.0:
#                 vertices = self.interpolate(vertices, interpolate)

#             vertices = self.transform(vertices)

#             result = np.zeros((len(vertices) * 3 - 2, 2), float)
#             codes = mpath.Path.CURVE4 * np.ones((len(vertices) * 3 - 2, ), mpath.Path.code_type)
#             result[0] = vertices[0]
#             codes[0] = mpath.Path.MOVETO

#             kappa = 4.0 * ((np.sqrt(2.0) - 1.0) / 3.0)
#             kappa = 0.5

#             p0   = vertices[0:-1]
#             p1   = vertices[1:  ]

#             x0   = p0[:, 0:1]
#             y0   = p0[:, 1: ]
#             b0   = ((y0 - x0) - y0) / ((x0 + y0) - x0)
#             a0   = y0 - b0*x0

#             x1   = p1[:, 0:1]
#             y1   = p1[:, 1: ]
#             b1   = ((y1 - x1) - y1) / ((x1 + y1) - x1)
#             a1   = y1 - b1*x1

#             x = -(a0-a1) / (b0-b1)
#             y = a0 + b0*x

#             xk = (x - x0) * kappa + x0
#             yk = (y - y0) * kappa + y0

#             result[1::3, 0:1] = xk
#             result[1::3, 1: ] = yk

#             xk = (x - x1) * kappa + x1
#             yk = (y - y1) * kappa + y1

#             result[2::3, 0:1] = xk
#             result[2::3, 1: ] = yk

#             result[3::3] = p1

#             print vertices[-2:]
#             print result[-2:]

#             return mpath.Path(result, codes)

#             twopi = 2.0 * np.pi
#             halfpi = 0.5 * np.pi

#             vertices = path.vertices
#             t0 = vertices[0:-1, 0]
#             t1 = vertices[1:  , 0]
#             td = np.where(t1 > t0, t1 - t0, twopi - (t0 - t1))
#             maxtd = td.max()
#             interpolate = np.ceil(maxtd / halfpi)

#             print "interpolate", interpolate
#             if interpolate > 1.0:
#                 vertices = self.interpolate(vertices, interpolate)

#             result = np.zeros((len(vertices) * 3 - 2, 2), float)
#             codes = mpath.Path.CURVE4 * np.ones((len(vertices) * 3 - 2, ), mpath.Path.code_type)
#             result[0] = vertices[0]
#             codes[0] = mpath.Path.MOVETO

#             kappa = 4.0 * ((np.sqrt(2.0) - 1.0) / 3.0)
#             tkappa = np.arctan(kappa)
#             hyp_kappa = np.sqrt(kappa*kappa + 1.0)

#             t0 = vertices[0:-1, 0]
#             t1 = vertices[1:  , 0]
#             r0 = vertices[0:-1, 1]
#             r1 = vertices[1:  , 1]

#             td = np.where(t1 > t0, t1 - t0, twopi - (t0 - t1))
#             td_scaled = td / (np.pi * 0.5)
#             rd = r1 - r0
#             r0kappa = r0 * kappa * td_scaled
#             r1kappa = r1 * kappa * td_scaled
#             ravg_kappa = ((r1 + r0) / 2.0) * kappa * td_scaled

#             result[1::3, 0] = t0 + (tkappa * td_scaled)
#             result[1::3, 1] = r0*hyp_kappa
#             # result[1::3, 1] = r0 / np.cos(tkappa * td_scaled) # np.sqrt(r0*r0 + ravg_kappa*ravg_kappa)

#             result[2::3, 0] = t1 - (tkappa * td_scaled)
#             result[2::3, 1] = r1*hyp_kappa
#             # result[2::3, 1] = r1 / np.cos(tkappa * td_scaled) # np.sqrt(r1*r1 + ravg_kappa*ravg_kappa)

#             result[3::3, 0] = t1
#             result[3::3, 1] = r1

#             print vertices[:6], result[:6], t0[:6], t1[:6], td[:6], td_scaled[:6], tkappa
#             result = self.transform(result)
#             return mpath.Path(result, codes)
#         transform_path_non_affine = transform_path
