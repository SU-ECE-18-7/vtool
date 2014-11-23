"""
python -c "import vtool, doctest; print(doctest.testmod(vtool.keypoint))"

Keypoints are stored in the invA format by default.
Unfortunately many places in the code reference this as A instead of invA
because I was confused when I first started writing this.

to rectify this I am changing terminology.

Variables:
    invV : maps from ucircle onto an ellipse (perdoch.invA)
       V : maps from ellipse to ucircle      (perdoch.A)
       Z : the conic matrix                  (perdoch.E)

Representation:
    kpts (ndarray) : [x, y, iv11, iv21, iv22, ori]
    invV (ndarray): [(iv11, iv12, x),
                     (iv21, iv22, y),
                     (   0,    0, 1),]

Efficiency Notes:
    single index indexing is very fast

    slicing seems to be very fast.

    fancy indexing with __getitem__ is very slow
    using np.take is a better idea, but its a bit harder
    to use with multidimensional arrays
"""
from __future__ import absolute_import, division, print_function
# Python
from six.moves import zip, range
# Science
import numpy as np
import numpy.linalg as npl
from numpy.core.umath_tests import matrix_multiply
from numpy import (array, rollaxis, sqrt, zeros, ones, diag)
# VTool
from vtool import linalg as ltool
from vtool import chip as ctool
from vtool import trig
# UTool
import utool
import utool as ut
#(print, print_, printDBG, rrr, profile) = utool.inject(__name__, '[kpts]')


"""
// These are cython style comments used for maintaining python compatibility
#if CYTH
from vtool.keypoint import get_invVR_mats_shape, get_invVR_mats_sqrd_scale, get_invVR_mats_oris

cdef np.float64_t TAU = 2 * np.pi
#endif
"""
#:%s/numpy_floatarray_\([13]\)dimension/np.ndarray[np.float64_t, ndim=\1]/gc
#:%s/np.ndarray\[np.float64_t, ndim=\([13]\)\]/numpy_floatarray_\1dimension/gc


TAU = 2 * np.pi  # References: tauday.com
GRAVITY_THETA = TAU / 4
KPTS_DTYPE = np.float32

XDIM = 0
YDIM = 1
SCAX_DIM = 2
SKEW_DIM = 3
SCAY_DIM = 4
ORI_DIM = 5
LOC_DIMS   = np.array([XDIM, YDIM])
SHAPE_DIMS = np.array([SCAX_DIM, SKEW_DIM, SCAY_DIM])


def get_grid_kpts(wh=(300, 300), wh_stride=(50, 50), scale=20, dtype=np.float32, **kwargs):
    """ Returns a regular grid of keypoints """
    (w, h) = wh
    (wstride, hstride) = wh_stride
    padding = scale * 1.5
    xbasis = np.arange(padding, (w - padding), wstride)
    ybasis = np.arange(padding, (h - padding), hstride)
    xs_grid, ys_grid = np.meshgrid(xbasis, ybasis)
    _xs = xs_grid.flatten()
    _ys = ys_grid.flatten()
    nKpts = len(_xs)
    _zeros = np.zeros(nKpts, dtype=dtype)
    _iv11s = _zeros + scale
    _iv21s = _zeros
    _iv22s = _zeros + scale
    _oris = _zeros
    kpts = np.vstack((_xs, _ys, _iv11s, _iv21s, _iv22s, _oris)).astype(dtype).T
    return kpts


# --- raw keypoint components ---
def get_xys(kpts):
    """ Keypoint locations in chip space """
    _xys = kpts.T[0:2]
    return _xys


def get_homog_xyzs(kpts_):
    """ Keypoint locations in chip space """
    _xys = get_xys(kpts_)
    _zs = np.ones(len(kpts_), dtype=kpts_.dtype)
    _xyzs = np.vstack((_xys, _zs))
    return _xyzs


def get_invVs(kpts):
    """ Keypoint shapes (oriented with the gravity vector) """
    _invVs = kpts.T[2:5]
    return _invVs


def get_oris(kpts):
    """ Extracts keypoint orientations for kpts array

    (in isotropic guassian space relative to the gravity vector)
    (in simpler words: the orientation is is taken from keypoints warped to the unit circle)

    Args:
        kpts (ndarray): (N x 6) [x, y, a, c, d, theta]

    Returns:
        (ndarray) theta
    """
    if kpts.shape[1] == 5:
        _oris = np.zeros(len(kpts), dtype=kpts.dtype)
    elif kpts.shape[1] == 6:
        _oris = kpts.T[5]
    else:
        raise AssertionError('[ktool] Invalid kpts.shape = %r' % (kpts.shape,))
    return _oris


# --- keypoint properties ---

def get_sqrd_scales(kpts):
    """ gets average squared scale (does not take into account elliptical shape """
    _iv11s, _iv21s, _iv22s = get_invVs(kpts)
    _scales_sqrd = np.multiply(_iv11s, _iv22s)
    return _scales_sqrd


def get_scales(kpts):
    """  Gets average scale (does not take into account elliptical shape """
    _scales = sqrt(get_sqrd_scales(kpts))
    return _scales


# --- keypoint matrixes ---

def get_ori_mats(kpts):
    """ Returns keypoint orientation matrixes """
    _oris = get_oris(kpts)
    R_mats = [ltool.rotation_mat2x2(ori) for ori in _oris]
    return R_mats


def get_invV_mats2x2(kpts, with_ori=False):
    """ Returns keypoint shape matrixes
        (default orientation is down)
    """
    nKpts = len(kpts)
    try:
        _iv11s, _iv21s, _iv22s = get_invVs(kpts)
        _zeros = zeros(nKpts)
        invV_arrs = array([[_iv11s, _zeros],
                           [_iv21s, _iv22s]])  # R x C x N
        invV_mats = rollaxis(invV_arrs, 2)     # N x R x C
    except ValueError as ex:
        ut.printex(ex, keys=['kpts', '_zeros', '_iv11s', '_iv21s', '_iv22s'])
        #ut.embed()
        #print(kpts)
        raise
    if with_ori:
        # You must apply rotations before you apply shape
        # This is because we are dealing with \emph{inv}(V).
        # numpy operates with data on the right (operate right-to-left)
        R_mats  = get_ori_mats(kpts)
        invV_mats = matrix_multiply(invV_mats, R_mats)
    return invV_mats


#@profile
def get_invV_mats(kpts, with_trans=False, with_ori=False, ashomog=False, ascontiguous=False):
    """
    packs keypoint shapes into affine invV matrixes
    (default is just the 2x2 shape. But translation, orientation,
    homogonous, and contiguous flags can be set.)

    Example:
        >>> from vtool.keypoint import *  # NOQA
        >>> kpts = np.array([[10, 20, 1, 2, 3, 0]])
        >>> with_trans=True
        >>> with_ori=True
        >>> ashomog=True
        >>> ascontiguous=False
        >>> innVR_mats = get_invV_mats(kpts, with_trans, with_ori, ashomog, ascontiguous)
        array([[[  1.,   0.,  10.],
                [  2.,   3.,  20.],
                [  0.,   0.,   1.]]])
    """
    nKpts = len(kpts)
    invV_mats = get_invV_mats2x2(kpts, with_ori=with_ori)
    if with_trans or ashomog:
        #_iv11s = invV_mats[:, 0, 0]
        #_iv12s = invV_mats[:, 0, 1]
        #_iv21s = invV_mats[:, 1, 0]
        #_iv22s = invV_mats[:, 1, 1]
        _iv11s = invV_mats.T[0, 0]
        _iv12s = invV_mats.T[1, 0]
        _iv21s = invV_mats.T[0, 1]
        _iv22s = invV_mats.T[1, 1]
        # Use homogenous coordinates
        _zeros = zeros(nKpts)
        _ones = ones(nKpts)
        if with_trans:
            _iv13s, _iv23s = get_xys(kpts)
        else:
            _iv13s = _iv23s = _zeros
        invV_arrs =  array([[_iv11s, _iv12s, _iv13s],
                            [_iv21s, _iv22s, _iv23s],
                            [_zeros, _zeros,  _ones]])  # R x C x N
        invV_mats = rollaxis(invV_arrs, 2)  # N x R x C
    if ascontiguous:
        invV_mats = np.ascontiguousarray(invV_mats)
    return invV_mats

# --- scaled and offset keypoint components ---


#@profile
def transform_kpts_to_imgspace(kpts, bbox, bbox_theta, chipsz):
    """ Transforms keypoints so they are plotable in imagespace
        kpts   - xyacdo keypoints
        bbox   - chip bounding boxes in image space
        theta  - chip rotationsinvC
        chipsz - chip extent (in keypoint / chip space)
    """
    # Get keypoints in matrix format
    invV_mats = get_invV_mats(kpts, with_trans=True, with_ori=True)
    # Get chip to imagespace transform
    invC = ctool._get_chip_to_image_transform(bbox, chipsz, bbox_theta)
    # Apply transform to keypoints
    invCinvV_mats = matrix_multiply(invC, invV_mats)
    # Flatten back into keypoint format
    imgkpts = flatten_invV_mats_to_kpts(invCinvV_mats)
    return imgkpts


#@profile
def offset_kpts(kpts, offset=(0.0, 0.0), scale_factor=1.0):
    if offset == (0.0, 0.0) and scale_factor == 1.0:
        return kpts
    M = ltool.scaleedoffset_mat3x3(offset, scale_factor)
    kpts_ = transform_kpts(kpts, M)
    return kpts_


#@profile
def transform_kpts(kpts, M):
    invV_mats = get_invV_mats(kpts, with_trans=True, with_ori=True)
    MinvV_mats = matrix_multiply(M, invV_mats)
    try:
        assert np.all(MinvV_mats[:, 2, 0:2] == 0)
        assert np.all(MinvV_mats[:, 2, 2] == 1)
    except AssertionError as ex:  # NOQA
        #print(ex)
        MinvV_mats = ltool.rowwise_division(MinvV_mats, MinvV_mats[:, 2, 2])
        #utool.printex(ex, 'WARNING: transform produced nonhomogonous keypoint')
    kpts_ = flatten_invV_mats_to_kpts(MinvV_mats)
    return kpts_

#---------------------
# invV_mats functions
#---------------------


#@profile
def get_invVR_mats_sqrd_scale(invVR_mats):
    """ Returns the squared scale of the invVR keyponts

    >>> from vtool.keypoint import *  # NOQA
    >>> np.random.seed(0)
    >>> invVR_mats = np.random.rand(1000, 3, 3).astype(np.float64)
    >>> output = get_invVR_mats_sqrd_scale(invVR_mats)
    >>> print(utool.hashstr(output))
    1i468l@838vatv@4

    #CYTH_INLINE
    #CYTH_RETURNS np.ndarray[np.float64_t, ndim=1]
    #CYTH_PARAM_TYPES:
        np.ndarray[np.float64_t, ndim=3] invVR_mats
    #if CYTH
    cdef unsigned int nMats = invVR_mats.shape[0]
    # Prealloc output
    cdef np.ndarray[np.float64_t, ndim=1] out = np.zeros((nMats,), dtype=np.float64)
    #cdef size_t ix
    cdef Py_ssize_t ix
    for ix in range(nMats):
        # simple determinant: ad - bc
        out[ix] = ((invVR_mats[ix, 0, 0] * invVR_mats[ix, 1, 1]) -
                   (invVR_mats[ix, 0, 1] * invVR_mats[ix, 1, 0]))
    return out
    #else
    """
    return npl.det(invVR_mats[:, 0:2, 0:2])
    "#endif"


#@profile
def get_invVR_mats_shape(invVR_mats):
    """ Extracts keypoint shape components

    >>> from vtool.keypoint import *  # NOQA
    >>> np.random.seed(0)
    >>> invVR_mats = np.random.rand(1000, 3, 3).astype(np.float64)
    >>> output = get_invVR_mats_shape(invVR_mats)
    >>> print(utool.hashstr(output))
    oq9o@yqhtgloy58!

    #CYTH_INLINE
    #if CYTH
    #CYTH_PARAM_TYPES:
        np.ndarray[np.float64_t, ndim=3] invVR_mats
    cdef:
        np.ndarray[np.float64_t, ndim=1] _iv11s
        np.ndarray[np.float64_t, ndim=1] _iv12s
        np.ndarray[np.float64_t, ndim=1] _iv21s
        np.ndarray[np.float64_t, ndim=1] _iv22s
        #double [:] _iv11s
        #double [:] _iv12s
        #double [:] _iv21s
        #double [:] _iv22s
    #endif
    """

    # TODO
    # http://stackoverflow.com/questions/14386822/fast-numpy-fancy-indexing
    #(a.ravel()[(cols + (rows * a.shape[1]).reshape((-1,1))).ravel()]).reshape(rows.size, cols.size)

    # So, this doesn't work
    # Try this instead
    #http://docs.cython.org/src/userguide/memoryviews.html#memoryviews
    '''
    '#if cyth'
    '#m_acro numpy_fancy_index_macro'
    '#e_ndmacro'
    _iv11s = invVR_mats.take(:, axis=1)
    _iv12s = invVR_mats[:, 0, 1]
    _iv21s = invVR_mats[:, 1, 0]
    _iv22s = invVR_mats[:, 1, 1]
    '#else'
    #cols, rows, dims = invVR_mats.shape
    #invVR_mats.ravel()[(cols + (rows * a.shape[1]).reshape((-1, 1))).ravel()])
    '''
    _iv11s = invVR_mats[:, 0, 0]
    _iv12s = invVR_mats[:, 0, 1]
    _iv21s = invVR_mats[:, 1, 0]
    _iv22s = invVR_mats[:, 1, 1]
    '#endif'

    #'#pragma cyth numpy_fancy_index_assign'
    return (_iv11s, _iv12s, _iv21s, _iv22s)


#@profile
def get_invVR_mats_xys(invVR_mats):
    r"""
    extracts locations
    extracts xys from matrix encoding

    Args:
        invVR_mats (ndarray) : list of matrices mapping ucircles to ellipses

    Returns:
        ndarray: the xy location

    Cyth:
        #if CYTH
        #CYTH_PARAM_TYPES:
            np.ndarray[np.float64_t, ndim=3] invVR_mats
        cdef:
            np.ndarray[np.float64_t, ndim=2] _xys
        #endif

    Timeit:
        >>> import utool
        >>> setup = utool.codeblock(
        ...     '''
                import numpy as np
                np.random.seed(0)
                invVR_mats = np.random.rand(1000, 3, 3).astype(np.float64)
                ''')
        >>> stmt_list = utool.codeblock(
        ...     '''
                invVR_mats[:, 0:2, 2].T
                invVR_mats.T[2, 0:2]
                invVR_mats.T.take(2, axis=0).take([0, 1], axis=0)
                invVR_mats.T.take(2, axis=0)[0:2]
                '''
        ... ).split('\n')
        >>> utool.util_dev.timeit_compare(stmt_list, setup, int(1E5))
        #>>> utool.util_dev.rrr()

    Example:
        >>> from vtool.keypoint import *  # NOQA
        >>> np.random.seed(0)
        >>> invVR_mats = np.random.rand(1000, 3, 3).astype(np.float64)
        >>> invVR_mats.T[2, 0:2]
    """
    # ORIG NUMPY
    #_xys = invVR_mats[:, 0:2, 2].T
    # BETTER NUMPY
    _xys = invVR_mats.T[2, 0:2]
    return _xys


#@profile
def get_invVR_mats_oris(invVR_mats):
    r""" extracts orientation from matrix encoding

    >>> from vtool.keypoint import *  # NOQA
    >>> np.random.seed(0)
    >>> invVR_mats = np.random.rand(1000, 2, 2).astype(np.float64)
    >>> output = get_invVR_mats_oris(invVR_mats)
    >>> print(utool.hashstr(output))
    mcoxq8!3ml5bj9rx

    Cyth:
        #CYTH_INLINE
        #CYTH_RETURNS np.ndarray[np.float64_t, ndim=1]
        #CYTH_PARAMS:
            np.ndarray[np.float64_t, ndim=3] invVR_mats
        #if CYTH
        cdef:
            np.ndarray[np.float64_t, ndim=1] _oris
            np.ndarray[np.float64_t, ndim=1] _iv12s
            np.ndarray[np.float64_t, ndim=1] _iv11s

        _iv11s = invVR_mats[:, 0, 0]
        _iv12s = invVR_mats[:, 0, 1]
        _oris = np.arctan2(_iv12s, _iv11s)  # outputs from -TAU/2 to TAU/2
        _oris[_oris < 0] = _oris[_oris < 0] + TAU  # map to 0 to TAU (keep coords)
        _oris = (-_oris) % TAU
        return _oris
        #else

    Timeit:
        >>> import utool
        >>> setup = utool.codeblock(
        ...     '''
                import numpy as np
                np.random.seed(0)
                invVR_mats = np.random.rand(10000, 2, 2).astype(np.float64)
                ''')
        >>> stmt_list = utool.codeblock(
        ...     '''
                invVR_mats[:, 0, 1]
                invVR_mats.T[1, 0]
                '''
        ... ).split('\n')
        >>> utool.util_dev.rrr()
        >>> utool.util_dev.timeit_compare(stmt_list, setup, int(1E3))
    """
    # Extract only the needed shape components
    #_iv11s = invVR_mats[:, 0, 0]
    #_iv12s = invVR_mats[:, 0, 1]
    _iv11s = invVR_mats.T[0, 0]
    _iv12s = invVR_mats.T[1, 0]
    # Solve for orientations. Adjust gravity vector pointing down
    _oris = (-trig.atan2(_iv12s, _iv11s)) % TAU
    return _oris
    "#endif"


#@profile
def rectify_invV_mats_are_up(invVR_mats):
    """
    Useful if invVR_mats is no longer lower triangular
    rotates affine shape matrixes into downward (lower triangular) position

    >>> from vtool.keypoint import *  # NOQA
    >>> np.random.seed(0)
    >>> invVR_mats = np.random.rand(1000, 2, 2).astype(np.float64)
    >>> output = rectify_invV_mats_are_up(invVR_mats)
    >>> print(utool.hashstr(output))
    2wir&6ybcga0bpvz

    #if CYTH
    # TODO: Template this for [float64_t, float32_t]
    #CYTH_PARAM_TYPES:
        np.ndarray[np.float64_t, ndim=3] invVR_mats
    cdef:
        np.ndarray[np.float64_t, ndim=3] invV_mats
        np.ndarray[np.float64_t, ndim=1] _oris
        np.ndarray[np.float64_t, ndim=1] _a
        np.ndarray[np.float64_t, ndim=1] _b
        np.ndarray[np.float64_t, ndim=1] _c
        np.ndarray[np.float64_t, ndim=1] _d
        np.ndarray[np.float64_t, ndim=1] det_
        np.ndarray[np.float64_t, ndim=1] b2a2
        np.ndarray[np.float64_t, ndim=1] iv11
        np.ndarray[np.float64_t, ndim=1] iv21
        np.ndarray[np.float64_t, ndim=1] iv22
    #endif
    """
    # Get orientation encoded in the matrix
    #_oris = get_invVR_mats_oris_cyth(invVR_mats)
    _oris = get_invVR_mats_oris(invVR_mats)
    # Extract keypoint shape components
    #(_a, _b, _c, _d) = get_invVR_mats_shape_cyth(invVR_mats)
    (_a, _b, _c, _d) = get_invVR_mats_shape(invVR_mats)
    #
    # Convert to lower triangular (rectify orientation downwards)
    det_ = np.sqrt(np.abs((_a * _d) - (_b * _c)))
    b2a2 = np.sqrt((_b ** 2) + (_a ** 2))
    iv11 = b2a2 / det_
    iv21 = ((_d * _b) + (_c * _a)) / (b2a2 * det_)
    iv22 = det_ / b2a2
    # Rebuild the matrixes
    invV_mats = invVR_mats.copy()
    invV_mats[:, 0, 0] = iv11 * det_
    invV_mats[:, 0, 1] = 0
    invV_mats[:, 1, 0] = iv21 * det_
    invV_mats[:, 1, 1] = iv22 * det_
    return invV_mats, _oris


#@profile
def flatten_invV_mats_to_kpts(invV_mats):
    """ flattens invV matrices into kpts format """
    invV_mats, _oris = rectify_invV_mats_are_up(invV_mats)
    _xs    = invV_mats[:, 0, 2]
    _ys    = invV_mats[:, 1, 2]
    _iv11s = invV_mats[:, 0, 0]
    _iv21s = invV_mats[:, 1, 0]
    assert np.all(invV_mats[:, 0, 1] == 0), 'expected lower triangular matrix'
    _iv22s = invV_mats[:, 1, 1]
    kpts = np.vstack((_xs, _ys, _iv11s, _iv21s, _iv22s, _oris)).T
    return kpts


#@profile
def get_V_mats(kpts, **kwargs):
    """
    Returns:
        V_mats (ndarray) : sequence of matrices that transform an ellipse to unit circle
    """
    invV_mats = get_invV_mats(kpts, **kwargs)
    V_mats = invert_invV_mats(invV_mats)
    return V_mats


#@profile
def get_Z_mats(V_mats):
    """
    transform into conic matrix Z
    Z = (V.T).dot(V)

    Returns:
        Z_mats (ndarray): Z is a conic representation of an ellipse
    """
    Vt_mats = array(list(map(np.transpose, V_mats)))
    Z_mats = matrix_multiply(Vt_mats, V_mats)
    return Z_mats


#@profile
def invert_invV_mats(invV_mats):
    try:
        V_mats = npl.inv(invV_mats)
    except npl.LinAlgError:
        # FIXME: !!!
        # Debug inverse
        V_mats_list = [None for _ in range(len(invV_mats))]
        for ix, invV in enumerate(invV_mats):
            try:
                V_mats_list[ix] = npl.inv(invV)
            except npl.LinAlgError:
                print(utool.hz_str('ERROR: invV_mats[%d] = ' % ix, invV))
                V_mats_list[ix] = np.nan(invV.shape)
        if utool.SUPER_STRICT:
            raise
        V_mats = np.array(V_mats_list)
    return V_mats


#@profile
def get_invV_xy_axis_extents(invV_mats):
    """ gets the scales of the major and minor elliptical axis.
        from invV_mats (faster)
    """
    if invV_mats.shape[1] == 3:
        # Take the SVD of only the shape part
        invV_mats = invV_mats[:, 0:2, 0:2]
    Us_list = [ltool.svd(invV)[0:2] for invV in invV_mats]
    def Us_axis_extent(U, s):
        """ Columns of U.dot(S) are in principle scaled directions """
        return sqrt(U.dot(diag(s)) ** 2).T.sum(0)
    xyexnts = array([Us_axis_extent(U, s) for U, s in Us_list])
    return xyexnts


#@profile
def get_xy_axis_extents(kpts):
    """ gets the scales of the major and minor elliptical axis
        from kpts (slower due to conversion to invV_mats)
    """
    invV_mats = get_invV_mats(kpts, ashomog=False)
    return get_invV_xy_axis_extents(invV_mats)


#@profile
def get_kpts_bounds(kpts):
    """ returns the width and height of keypoint bounding box """
    xs, ys = get_xys(kpts)
    xyexnts = get_xy_axis_extents(kpts)
    width = (xs + xyexnts.T[0]).max()
    height = (ys + xyexnts.T[1]).max()
    return (width, height)


#@profile
def get_diag_extent_sqrd(kpts):
    """ Returns the diagonal extent of keypoint locations """
    xs, ys = get_xys(kpts)
    x_extent_sqrd = (xs.max() - xs.min()) ** 2
    y_extent_sqrd = (ys.max() - ys.min()) ** 2
    extent_sqrd = x_extent_sqrd + y_extent_sqrd
    return extent_sqrd


#@profile
def cast_split(kpts, dtype=KPTS_DTYPE):
    """ breakup keypoints into location, shape, and orientation """
    kptsT = kpts.T
    _xs   = array(kptsT[0], dtype=dtype)
    _ys   = array(kptsT[1], dtype=dtype)
    _invVs = array(kptsT[2:5], dtype=dtype)
    if kpts.shape[1] == 6:
        _oris = array(kptsT[5:6], dtype=dtype)
    else:
        _oris = zeros(len(kpts))
    return _xs, _ys, _invVs, _oris


# --- strings ---

#@profile
def get_xy_strs(kpts):
    """ strings debugging and output """
    _xs, _ys   = get_xys(kpts)
    xy_strs = [('xy=(%.1f, %.1f)' % (x, y,)) for x, y, in zip(_xs, _ys)]
    return xy_strs


#@profile
def get_shape_strs(kpts):
    """ strings debugging and output """
    invVs = get_invVs(kpts)
    shape_strs  = [(('[(%3.1f,  0.00),\n' +
                     ' (%3.1f, %3.1f)]') % (iv11, iv21, iv22,))
                   for iv11, iv21, iv22 in zip(*invVs)]
    shape_strs = ['invV=\n' +  _str for _str in shape_strs]
    return shape_strs


#@profile
def get_ori_strs(kpts):
    _oris = get_oris(kpts)
    ori_strs = ['ori=' + utool.theta_str(ori) for ori in _oris]
    return ori_strs


#@profile
def get_kpts_strs(kpts):
    xy_strs = get_xy_strs(kpts)
    shape_strs = get_shape_strs(kpts)
    ori_strs = get_ori_strs(kpts)
    kpts_strs = ['\n---\n'.join(tup) for tup in zip(xy_strs, shape_strs, ori_strs)]
    return kpts_strs

try:
    import cyth
    if cyth.DYNAMIC:
        exec(cyth.import_cyth_execstr(__name__))
    else:
        # <AUTOGEN_CYTH>
        # Regen command: python -c "import vtool.keypoint" --cyth-write
        pass
        # </AUTOGEN_CYTH>
except ImportError as ex:
    pass
