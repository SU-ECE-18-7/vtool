from __future__ import absolute_import, division, print_function
import numpy as np
import utool as ut
(print, print_, printDBG, rrr, profile) = ut.inject(
    __name__, '[dist]', DEBUG=False)
#profile = utool.profile


TAU = 2 * np.pi  # References: tauday.com


@profile
def ori_distance(ori1, ori2):
    """ Returns how far off determinants are from one another
    >>> from vtool.linalg import *  # NOQA
    >>> np.random.seed(53)
    >>> ori1 = (np.random.rand(10) * TAU) - np.pi
    >>> ori2 = (np.random.rand(10) * TAU) - np.pi
    >>> output = ut.hashstr(ut.hashstr(ori_distance(ori1, ori2)))
    >>> print(ut.hashstr(output))
    !755pt!alrfgshiu

    Cyth:
        #if CYTH
        #CYTH_INLINE
        #CYTH_PARAM_TYPES:
            np.ndarray ori1
            np.ndarray ori2
        #endif

    Timeit:
        >>> import utool as ut
        >>> setup = ut.codeblock(
        ...     '''
                import numpy as np
                TAU = np.pi * 2
                np.random.seed(53)
                ori1 = (np.random.rand(100000) * TAU) - np.pi
                ori2 = (np.random.rand(100000) * TAU) - np.pi

                def func_outvars():
                    ori_dist = np.abs(ori1 - ori2)
                    np.mod(ori_dist, TAU, out=ori_dist)
                    np.minimum(ori_dist, np.subtract(TAU, ori_dist), out=ori_dist)
                    return ori_dist

                def func_orig():
                    ori_dist = np.abs(ori1 - ori2) % TAU
                    ori_dist = np.minimum(ori_dist, TAU - ori_dist)
                    return ori_dist
                ''')
        >>> stmt_list = ut.codeblock(
        ...     '''
                func_outvars()
                func_orig()
                '''
        ... ).split('\n')
        >>> ut.util_dev.rrr()
        >>> ut.util_dev.timeit_compare(stmt_list, setup, int(1E3))

    """
    # TODO: Cython
    # TODO: Outvariable
    ori_dist = np.abs(ori1 - ori2)
    np.mod(ori_dist, TAU, out=ori_dist)
    np.minimum(ori_dist, np.subtract(TAU, ori_dist), out=ori_dist)
    return ori_dist


@profile
def det_distance(det1, det2):
    """ Returns how far off determinants are from one another

    Example:
        >>> # ENABLE_DOCTEST
        >>> from vtool.linalg import *  # NOQA
        >>> np.random.seed(53)
        >>> det1 = np.random.rand(1000)
        >>> det2 = np.random.rand(1000)
        >>> output = det_distance(det1, det2)
        >>> result = ut.hashstr(output)
        >>> print(result)
        pfce!exwvqz8e1n!

    Cyth::
        #CYTH_INLINE
        #CYTH_RETURNS np.ndarray[np.float64_t, ndim=1]
        #CYTH_PARAM_TYPES:
            np.ndarray[np.float64_t, ndim=1] det1
            np.ndarray[np.float64_t, ndim=1] det2
        #if CYTH
        # TODO: Move to ktool?
        cdef unsigned int nDets = det1.shape[0]
        # Prealloc output
        out = np.zeros((nDets,), dtype=det1.dtype)
        cdef size_t ix
        for ix in range(nDets):
            # simple determinant: ad - bc
            if det1[ix] > det2[ix]:
                out[ix] = det1[ix] / det2[ix]
            else:
                out[ix] = det2[ix] / det1[ix]
        return out
        #else
    """
    # TODO: Cython
    det_dist = det1 / det2
    # Flip ratios that are less than 1
    _flip_flag = det_dist < 1
    #det_dist[_flip_flag] = (1.0 / det_dist[_flip_flag])
    det_dist[_flip_flag] = np.reciprocal(det_dist[_flip_flag])
    return det_dist


@profile
def L1(hist1, hist2):
    """ returns L1 (aka manhatten or grid) distance between two histograms """
    return (np.abs(hist1 - hist2)).sum(-1)


@profile
def L2_sqrd(hist1, hist2):
    """ returns the squared L2 distance

    SeeAlso:
        L2

    Example:
        >>> # ENABLE_DOCTEST
        >>> from vtool.linalg import *  # NOQA
        >>> np.random.seed(53)
        >>> hist1 = np.random.rand(1000, 2)
        >>> hist2 = np.random.rand(1000, 2)
        >>> output = L2_sqrd(hist1, hist2)
        >>> result = ut.hashstr(output)
        >>> print(result)
        v9wc&brmvjy1as!z

    Cyth::
        #CYTH_INLINE
        #CYTH_RETURNS np.ndarray[np.float64_t, ndim=1]
        #CYTH_PARAM_TYPES:
            np.ndarray[np.float64_t, ndim=2] hist1
            np.ndarray[np.float64_t, ndim=2] hist2
        #if CYTH
        cdef:
            size_t cx, rx
        cdef unsigned int rows = hist1.shape[0]
        cdef unsigned int cols = hist1.shape[1]
        # Prealloc output
        cdef np.ndarray[np.float64_t, ndim=1] out = np.zeros((rows,), dtype=hist1.dtype)
        for rx in range(rows):
            for cx in range(cols):
                out[rx] += (hist1[rx, cx] - hist2[rx, cx]) ** 2
        return out
        #else
    """
    # TODO: np.ufunc
    # TODO: Cython
    # temp memory
    #temp = np.empty(hist1.shape, dtype=hist1.dtype)
    #np.subtract(hist1, hist2, temp)
    #np.abs(temp, temp)
    #np.power(temp, 2, temp)
    #out = temp.sum(-1)
    return ((hist1 - hist2) ** 2).sum(-1)  # this is faster
    #return out


@profile
def L2(hist1, hist2):
    """ returns L2 (aka euclidean or standard) distance between two histograms """
    return np.sqrt((np.abs(hist1 - hist2) ** 2).sum(-1))


@profile
def hist_isect(hist1, hist2):
    """ returns histogram intersection distance between two histograms """
    numer = (np.dstack([hist1, hist2])).min(-1).sum(-1)
    denom = hist2.sum(-1)
    hisect_dist = 1 - (numer / denom)
    if len(hisect_dist) == 1:
        hisect_dist = hisect_dist[0]
    return hisect_dist


if __name__ == '__main__':
    """
    CommandLine:
        python -m vtool.distance
        python -m vtool.distance --allexamples
        python -m vtool.distance --allexamples --noface --nosrc
    """
    import multiprocessing
    multiprocessing.freeze_support()  # for win32
    import utool as ut  # NOQA
    ut.doctest_funcs()