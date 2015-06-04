# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function
import utool
import numpy as np
import utool as ut
import six
import scipy.interpolate
print, rrr, profile = utool.inject2(__name__, '[scorenorm]', DEBUG=False)


def check_unused_kwargs(kwargs, expected_keys):
    unused_keys = set(kwargs.keys()) - set(expected_keys)
    if len(unused_keys) > 0:
        print('unused kwargs keys = %r' % (unused_keys))


@six.add_metaclass(ut.ReloadingMetaclass)
class ScoreNormalizer(object):
    """
    Conforms to scikit-learn Estimator interface
    """
    def __init__(encoder, **kwargs):
        encoder.learn_kw = ut.update_existing(
            dict(
                gridsize=1024,
                adjust=8,
                #monotonize=False,
                monotonize=True,
                #clip_factor=(ut.PHI + 1),
                clip_factor=None,
                reverse=None,
            ), kwargs)
        check_unused_kwargs(kwargs, encoder.learn_kw.keys())
        # Target recall for learned threshold
        encoder.target_tpr = .95
        # Support data
        encoder.support = dict(
            tp_support=None,
            tn_support=None,
        )
        # Learned score normalization
        encoder.score_domain     = None
        encoder.p_tp_given_score = None
        encoder.p_tn_given_score = None
        encoder.p_score_given_tn = None
        encoder.p_score_given_tp = None
        encoder.p_score = None
        # Learneed classification threshold
        encoder.learned_thresh   = None

    def fit(encoder, X, y):
        """
        Fits estimator to data.

        Args:
            X (ndarray):   1 dimensional scores
            y (ndarray): True or False labels

        CommandLine:
            python -m vtool.score_normalization --test-fit --show

        Example:
            >>> # ENABLE_DOCTEST
            >>> from vtool.score_normalization import *  # NOQA
            >>> import vtool as vt
            >>> encoder = ScoreNormalizer()
            >>> X, y = vt.tests.dummy.testdata_binary_scores()
            >>> result = encoder.fit(X, y)
            >>> print(result)
            >>> ut.quit_if_noshow()
            >>> encoder.visualize()
            >>> ut.show_if_requested()
        """
        encoder.learn_probabilities(X, y)
        encoder.learn_threshold(X, y)

    def learn_probabilities(encoder, X, y):
        tp_support = X.compress(y, axis=0).astype(np.float64)
        tn_support = X.compress(np.logical_not(y), axis=0).astype(np.float64)
        encoder.support['tp_support'] = tp_support
        encoder.support['tn_support'] = tn_support

        if encoder.learn_kw['reverse'] is None:
            # heuristic
            encoder.learn_kw['reverse'] = tp_support.mean() < tn_support.mean()
            print('[scorenorm] setting reverse = %r' % (encoder.learn_kw['reverse']))

        tup = learn_score_normalization(tp_support, tn_support, return_all=True, **encoder.learn_kw)
        (score_domain, p_tp_given_score, p_tn_given_score, p_score_given_tp,
         p_score_given_tn, p_score) = tup

        encoder.score_domain = score_domain
        encoder.p_tp_given_score = p_tp_given_score
        encoder.p_tn_given_score = p_tn_given_score
        encoder.p_score_given_tn = p_score_given_tn
        encoder.p_score_given_tp = p_score_given_tp
        encoder.p_score = p_score

        encoder.interp_fn = scipy.interpolate.interp1d(
            encoder.score_domain, encoder.p_tp_given_score, kind='linear',
            copy=False, assume_sorted=False)

    def learn_threshold(encoder, X, y):
        # select a cutoff threshold
        import sklearn.metrics
        probs = encoder.normalize_scores(X)
        fpr_curve, tpr_curve, thresholds = sklearn.metrics.roc_curve(y, probs, pos_label=True)
        # Select threshold that gives 95% recall (we should optimize this for a tradeoff)
        index = np.where(tpr_curve > .95)[0][0]
        encoder.learned_thresh = thresholds[index]
        print('[scorenorm] learned_thresh = %r' % (encoder.learned_thresh,))
        print('[scorenorm] score_thresh = %r' % (encoder.inverse_normalize(encoder.learned_thresh),))

    def inverse_normalize(encoder, probs):
        inverse_interp = scipy.interpolate.interp1d(encoder.p_tp_given_score, encoder.score_domain,
                                                    kind='linear', copy=False,
                                                    assume_sorted=False)
        scores = inverse_interp(probs)
        return scores

    def normalize_scores(encoder, X):
        is_iterable = ut.isiterable(X)
        if not is_iterable:
            X = np.array([X])
        prob = normalize_scores(encoder.score_domain, encoder.p_tp_given_score, X, interp_fn=encoder.interp_fn)
        if not is_iterable:
            prob = prob[0]
        return prob

    def predict(encoder, X):
        """ Predict true or false of ``X``. """
        prob = encoder.normalize_scores(X)
        pred = prob > encoder.learned_thresh
        return pred

    def get_accuracy(encoder, X, y):
        pred = encoder.predict(X)
        is_correct = pred == y
        accuracy = (is_correct).mean()
        return accuracy

    def get_error_indicies(encoder, X, y):
        """
        Returns the indicies of the most difficult type I and type II errors.
        """
        prob = encoder.normalize_scores(X)
        pred = prob > encoder.learned_thresh
        is_correct = pred == y
        # Find misspredictions
        is_error = np.logical_not(is_correct)
        # get indexes of misspredictions
        error_indicies = np.where(is_error)[0]
        # Separate by Type I and Type II error
        error_y = y.take(error_indicies)
        fp_indicies_ = error_indicies.compress(np.logical_not(error_y))
        fn_indicies_ = error_indicies.compress(error_y)
        # Sort errors by difficulty
        fp_sortx = prob.take(fp_indicies_).argsort()[::-1]
        fn_sortx = prob.take(fn_indicies_).argsort()
        fp_indicies = fp_indicies_.take(fp_sortx)
        fn_indicies = fn_indicies_.take(fn_sortx)
        return fp_indicies, fn_indicies

    def visualize(encoder, **kwargs):
        inspect_kw = ut.update_existing(
            dict(
                with_scores=True,
                with_roc=True,
                with_precision_recall=True,
                fnum=None,
                figtitle=None,
                interactive=None,
            ), kwargs)
        prob_thresh = encoder.learned_thresh
        score_thresh = encoder.inverse_normalize(prob_thresh)
        inter = inspect_pdfs(
            encoder.support['tn_support'], encoder.support['tp_support'],
            encoder.score_domain, encoder.p_tp_given_score,
            encoder.p_tn_given_score, encoder.p_score_given_tp,
            encoder.p_score_given_tn, encoder.p_score, prob_thresh=prob_thresh,
            score_thresh=score_thresh, **inspect_kw)
        import plottool as pt
        pt.adjust_subplots(bottom=.06, left=.06, right=.97, wspace=.25, hspace=.25, top=.9)
        return inter


def learn_score_normalization(tp_support, tn_support,
                              gridsize=1024,
                              adjust=8, return_all=False, monotonize=True,
                              clip_factor=(ut.PHI + 1), verbose=True, reverse=False):
    r"""
    Takes collected data and applys parzen window density estimation and bayes rule.

    #True positive scores must be larger than true negative scores.

    Args:
        tp_support (ndarray):
        tn_support (ndarray):
        gridsize       (int): default 512
        adjust         (int): default 8
        return_all     (bool): default False
        monotonize     (bool): default True
        clip_factor    (float): default phi ** 2

    Returns:
        tuple: (score_domain, p_tp_given_score, p_tn_given_score, p_score_given_tp, p_score_given_tn, p_score)

    Example:
        >>> # ENABLE_DOCTEST
        >>> from ibeis.model.hots.score_normalization import *  # NOQA
        >>> tp_support = np.linspace(100, 10000, 512)
        >>> tn_support = np.linspace(0, 120, 512)
        >>> (score_domain, p_tp_given_score) = learn_score_normalization(tp_support, tn_support)
        >>> result = int(p_tp_given_score.sum())
        >>> print(result)
        92
    """
    # Estimate true positive density
    if verbose:
        print('[scorenorm] estimating true positive pdf, tp_support.shape=%r' % (tp_support.shape,))
    score_tp_pdf = ut.estimate_pdf(tp_support, gridsize=gridsize, adjust=adjust)
    if verbose:
        print('[scorenorm] estimating true negative pdf, tn_support.shape=%r' % (tn_support.shape,))
    score_tn_pdf = ut.estimate_pdf(tn_support, gridsize=gridsize, adjust=adjust)
    if verbose:
        print('[scorenorm] estimating score domain')
    # Find good score domain range
    min_score, max_score = find_clip_range(tp_support, tn_support, clip_factor, reverse)
    score_domain = np.linspace(min_score, max_score, gridsize)
    # Evaluate true negative density
    if verbose:
        print('[scorenorm] evaluating density')
    p_score_given_tp = score_tp_pdf.evaluate(score_domain)
    p_score_given_tn = score_tn_pdf.evaluate(score_domain)
    if verbose:
        print('[scorenorm] evaluating posterior probabilities')
    # Average to get probablity of any score
    p_score = (np.array(p_score_given_tp) + np.array(p_score_given_tn)) / 2.0
    # Apply bayes
    # FIXME: not always going to be equal probability of true and positive cases
    p_tp = .5
    p_tp_given_score = ut.bayes_rule(p_score_given_tp, p_tp, p_score)
    import vtool as vt
    if monotonize:
        if verbose:
            print('[scorenorm] monotonizing')
        if reverse:
            p_tp_given_score = vt.ensure_monotone_strictly_decreasing(
                p_tp_given_score, left_endpoint=1.0, right_endpoint=0.0)
        else:
            p_tp_given_score = vt.ensure_monotone_strictly_increasing(
                p_tp_given_score, left_endpoint=0.0, right_endpoint=1.0)
    if return_all:
        p_tn_given_score = 1 - p_tp_given_score
        return (score_domain, p_tp_given_score, p_tn_given_score,
                p_score_given_tp, p_score_given_tn, p_score)
    else:
        return (score_domain, p_tp_given_score)


def find_clip_range(tp_support, tn_support, clip_factor=ut.PHI + 1, reverse=None):
    """
    TODO: generalize to arbitrary domains (not just 0->inf)

    Finds score to clip true positives past. This is useful when the highest
    true positive scores can be much larger than the highest true negative
    score.

    Args:
        tp_support (ndarray):
        tn_support (ndarray):
        clip_factor (float): factor of the true negative domain to search for true positives

    Returns:
        tuple: min_score, max_score

    CommandLine:
        python -m vtool.score_normalization --test-find_clip_range

    Example:
        >>> # ENABLE_DOCTEST
        >>> from vtool.score_normalization import *  # NOQA
        >>> tp_support = np.array([100, 200, 50000])
        >>> tn_support = np.array([10, 30, 110])
        >>> clip_factor = ut.PHI + 1
        >>> min_score, max_score = find_clip_range(tp_support, tn_support,  clip_factor)
        >>> result = '%.4f, %.4f' % ((min_score, max_score))
        >>> print(result)
        100.0000, 287.9837
    """
    if reverse is None:
        mean_tp_score = tp_support.mean()
        mean_tn_score = tn_support.mean()
        reverse = mean_tp_score < mean_tn_score

    if not reverse:
        # Normal case where higher scores is better
        high_scores = tp_support
        low_scores  = tn_support
    else:
        high_scores = tn_support
        low_scores  = tp_support

    max_high_score = high_scores.max()
    max_low_score  = low_scores.max()
    min_high_score = high_scores.min()
    min_low_score  = low_scores.min()
    abs_max_score = max(max_high_score, max_low_score)
    abs_min_score = min(min_high_score, min_low_score)

    if clip_factor is None:
        min_score = abs_min_score
        max_score = abs_max_score
        return min_score, max_score

    # FIXME: allow for true positive scores to be low, or not bounded at 0

    # Do not clip if true negatives can score higher than true positives
    if max_low_score < max_high_score:
        #overshoot_factor = (max_high_score - abs_min_score) / (max_low_score - abs_min_score)
        overshoot_factor = max_high_score / max_low_score
        if overshoot_factor > clip_factor:
            max_score = max_low_score * clip_factor
        else:
            max_score = max_high_score
    min_score = abs_min_score
    #if min_low_score < min_high_score:
    #    overshoot_factor = min_low_score / min_high_score
    #    if overshoot_factor > clip_factor:
    #        min_score = min_high_score * clip_factor
    #    else:
    #        min_score = min_low_score
    return min_score, max_score


def normalize_scores(score_domain, p_tp_given_score, scores, interp_fn=None):
    """
    Adjusts a raw scores to a probabilities based on a learned normalizer

    Args:
        score_domain (ndarray): input score domain
        p_tp_given_score (ndarray): learned probability mapping
        scores (ndarray): raw scores

    Returns:
        ndarray: probabilities

    CommandLine:
        python -m vtool.score_normalization --test-normalize_scores

    Example:
        >>> # ENABLE_DOCTEST
        >>> from vtool.score_normalization import *  # NOQA
        >>> score_domain = np.linspace(0, 10, 10)
        >>> p_tp_given_score = (score_domain ** 2) / (score_domain.max() ** 2)
        >>> scores = np.array([-1, 0.0, 0.01, 2.3, 8.0, 9.99, 10.0, 10.1, 11.1])
        >>> prob = normalize_scores(score_domain, p_tp_given_score, scores)
        >>> #np.set_printoptions(suppress=True)
        >>> result = ut.numpy_str(prob, precision=2, suppress_small=True)
        >>> print(result)
        >>> ut.quit_if_noshow()
        >>> import plottool as pt
        >>> pt.plot2(score_domain, p_tp_given_score, 'r-x', equal_aspect=False, label='learned probability')
        >>> pt.plot2(scores, prob, 'yo', equal_aspect=False, title='Normalized scores', pad=.2, label='query points')
        >>> pt.legend('upper left')
        >>> ut.show_if_requested()
        np.array([ 0.  ,  0.  ,  0.  ,  0.05,  0.64,  1.  ,  1.  ,  1.  ,  1.  ], dtype=np.float64)
    """
    #prob = np.zeros(len(scores))
    prob = np.zeros(len(scores))
    #prob = np.full(len(scores), np.nan)
    is_nan  = np.isnan(scores)
    # Check score domain bounds
    is_low  = scores < score_domain[0]
    is_high = scores > score_domain[-1]
    is_inbounds = np.logical_not(np.logical_or.reduce((is_nan, is_low, is_high)))
    # interpolate scores in the learned domain
    # we are garuenteed to have inbounds nonzero elements here
    if True:
        if interp_fn is None:
            # TODO build custom interpolator with correct bound checks
            interp_fn = scipy.interpolate.interp1d(score_domain, p_tp_given_score,
                                                   kind='linear', copy=False,
                                                   assume_sorted=True)
        prob[is_inbounds] = interp_fn(scores[is_inbounds])
    else:
        flags = score_domain <= scores[is_inbounds][:, None]
        left_indicies = np.array([np.nonzero(row)[0][-1] for row in flags])
        prob[is_inbounds] = p_tp_given_score[left_indicies]
    # currently just taking the min
    # fill in other values
    assert not np.any(is_nan), 'cannot normalize nan values'
    #if any(is_nan):
    #    # handle nans
    #    raise AssertionError('user normalize score list')
    #    prob[np.isnan(score_domain)] = -1.0
    # clip low scores at 0
    prob[is_low] = 0
    # clip high scores by between max probability and one
    prob[is_high] = (p_tp_given_score[-1] + 1.0) / 2.0
    return prob


# --------
# Plotting
# --------


def inspect_pdfs(tn_support, tp_support, score_domain, p_tp_given_score,
                 p_tn_given_score, p_score_given_tp, p_score_given_tn, p_score,
                 prob_thresh=None, score_thresh=None,
                 with_scores=False, with_roc=False,
                 with_precision_recall=False, fnum=None, figtitle=None, interactive=None):
    """
    Shows plots of learned thresholds
    """
    import plottool as pt  # NOQA

    if fnum is None:
        fnum = pt.next_fnum()

    with_normscore = True
    with_prebayes = True
    with_postbayes = True

    nSubplots = (with_normscore + with_prebayes + with_postbayes +
                 with_scores + with_roc + with_precision_recall)
    if True:
        nRows, nCols = pt.get_square_row_cols(nSubplots)
    else:
        nRows = nSubplots
        nCols = 1
    _pnumiter = pt.make_pnum_nextgen(nRows=nRows, nCols=nCols, nSubplots=nSubplots)

    import plottool.interactions

    inter = plottool.interactions.ExpandableInteraction(fnum, _pnumiter)

    import vtool as vt
    scores = np.hstack([tn_support, tp_support])
    labels = np.array([False] * len(tn_support) + [True] * len(tp_support))

    #c
    # probs = encoder.normalize_scores(scores)
    probs = normalize_scores(score_domain, p_tp_given_score, scores)

    confusions = vt.get_confusion_metrics(probs, labels)
    #print('fpr@.95 recall = %r' % (confusions.get_fpr_at_95_recall(),))
    print('fpp@95 recall = %05.2f%%' % (confusions.get_fpr_at_95_recall() * 100,))

    def _support(fnum, pnum):
        plot_support(tn_support, tp_support, fnum=fnum, pnum=pnum,
                     markersizes=[5, 5], score_markers=['^', 'v'],
                     score_label='score', threshold_value=score_thresh)

    def _normalized_support(fnum, pnum):
        tp_probs = probs[labels]
        tn_probs = probs[np.logical_not(labels)]
        plot_support(tn_probs, tp_probs, fnum=fnum, pnum=pnum,
                     markersizes=[5, 5], score_markers=['^', 'v'],
                     score_label='prob', threshold_value=prob_thresh)
        #ax = pt.gca()
        #max_score = max(tn_support.max(), tp_support.max())
        #ax.set_ylim(-max_score, max_score)

    def _prebayes(fnum, pnum):
        plot_prebayes_pdf(score_domain, p_score_given_tn, p_score_given_tp, p_score,
                          cfgstr='', fnum=fnum, pnum=pnum)

    def _postbayes(fnum, pnum):
        plot_postbayes_pdf(score_domain, p_tn_given_score, p_tp_given_score, prob_thresh=prob_thresh,
                           cfgstr='', fnum=fnum, pnum=pnum)
    def _roc(fnum, pnum):
        confusions.draw_roc_curve(fnum=fnum, pnum=pnum)

    def _precision_recall(fnum, pnum):
        confusions.draw_precision_recall_curve(fnum=fnum, pnum=pnum)

    if with_scores:
        inter.append_plot(_support)
    if with_prebayes:
        inter.append_plot(_prebayes)
    if with_postbayes:
        inter.append_plot(_postbayes)
    if with_normscore:
        inter.append_plot(_normalized_support)
    if with_roc:
        inter.append_plot(_roc)
    if with_precision_recall:
        inter.append_plot(_precision_recall)

    inter.show_page()

    if figtitle is not None:
        pt.set_figtitle(figtitle)

    return inter


def plot_support(tn_support, tp_support, fnum=None, pnum=(1, 1, 1), score_label='score', **kwargs):
    r"""
    Args:
        tn_support (ndarray):
        tp_support (ndarray):
        fnum (int):  figure number
        pnum (tuple):  plot number

    CommandLine:
        python -m ibeis.model.hots.score_normalization --test-plot_support

    Example:
        >>> # DISABLE_DOCTEST
        >>> from ibeis.model.hots.score_normalization import *  # NOQA
        >>> tn_support = '?'
        >>> tp_support = '?'
        >>> fnum = None
        >>> pnum = (1, 1, 1)
        >>> result = plot_support(tn_support, tp_support, fnum, pnum)
        >>> print(result)
    """
    import plottool as pt  # NOQA
    if fnum is None:
        fnum = pt.next_fnum()
    true_color = pt.TRUE_BLUE  # pt.TRUE_GREEN
    false_color = pt.FALSE_RED
    pt.plots.plot_sorted_scores(
        (tn_support, tp_support),
        ('trueneg', 'truepos'),
        score_colors=(false_color, true_color),
        #logscale=True,
        logscale=False,
        fnum=fnum,
        pnum=pnum,
        score_label=score_label,
        **kwargs)


def plot_prebayes_pdf(score_domain, p_score_given_tn, p_score_given_tp, p_score,
                      cfgstr='', fnum=None, pnum=(1, 1, 1)):
    import plottool as pt  # NOQA
    if fnum is None:
        fnum = pt.next_fnum()
    true_color = pt.TRUE_BLUE  # pt.TRUE_GREEN
    false_color = pt.FALSE_RED
    #unknown_color = pt.UNKNOWN_PURP
    unknown_color = pt.PURPLE2
    #unknown_color = pt.GRAY

    pt.plots.plot_probabilities(
        (p_score_given_tn,  p_score_given_tp, p_score),
        ('p(score | tn)', 'p(score | tp)', 'p(score)'),
        prob_colors=(false_color, true_color, unknown_color),
        figtitle='pre_bayes pdf score',
        xdata=score_domain,
        fnum=fnum,
        pnum=pnum)


def plot_postbayes_pdf(score_domain, p_tn_given_score, p_tp_given_score, prob_thresh=None,
                       cfgstr='', fnum=None, pnum=(1, 1, 1)):
    import plottool as pt  # NOQA
    if fnum is None:
        fnum = pt.next_fnum()
    true_color = pt.TRUE_BLUE  # pt.TRUE_GREEN
    false_color = pt.FALSE_RED

    pt.plots.plot_probabilities(
        (p_tn_given_score, p_tp_given_score),
        ('p(tn | score)', 'p(tp | score)'),
        prob_colors=(false_color, true_color,),
        figtitle='post_bayes pdf score ' + cfgstr,
        xdata=score_domain, fnum=fnum, pnum=pnum,
        prob_thresh=prob_thresh)


# DEBUGGING FUNCTIONS


def test_score_normalization(tp_support, tn_support, with_scores=True,
                             verbose=True, with_roc=True,
                             with_precision_recall=False, figtitle=None, normkw_varydict=None):
    """
    Gives an overview of how well threshold can be learned from raw scores.

    CommandLine:
        python -m vtool.score_normalization --test-test_score_normalization --show

    Example:
        >>> # ENABLE_DOCTEST
        >>> # Shows how score normalization works with gaussian noise
        >>> from vtool.score_normalization import *  # NOQA
        >>> verbose = True
        >>> randstate = np.random.RandomState(seed=0)
        >>> # Get a training sample
        >>> tp_support = randstate.normal(loc=6.5, size=(256,))
        >>> tn_support = randstate.normal(loc=3.5, size=(256,))
        >>> test_score_normalization(tp_support, tn_support, verbose=verbose)
        >>> ut.show_if_requested()

    """
    import plottool as pt  # NOQA

    # Print raw score statistics
    ut.print_stats(tp_support, lbl='tp_support')
    ut.print_stats(tn_support, lbl='tn_support')

    # Test (potentially multiple) normalizing configurations
    if normkw_varydict is None:
        normkw_varydict = {
            'monotonize': [False],  # [True, False],
            #'adjust': [1, 4, 8],
            'adjust': [1],
            #'adjust': [8],
        }
    normkw_list = ut.util_dict.all_dict_combinations(normkw_varydict)

    if len(normkw_list) > 32:
        raise AssertionError('Too many plots to test!')

    #plot_support(tn_support, tp_support, fnum=fnum)

    for normkw in normkw_list:
        # Learn the appropriate normalization
        tup = learn_score_normalization(tp_support, tn_support,
                                         return_all=True, verbose=verbose,
                                         **normkw)
        (score_domain,
         p_tp_given_score, p_tn_given_score,
         p_score_given_tp, p_score_given_tn,
         p_score) = tup

        if verbose:
            print('plotting pdfs')
        fnum = pt.next_fnum()

        inspect_pdfs(tn_support, tp_support, score_domain, p_tp_given_score,
                     p_tn_given_score, p_score_given_tp, p_score_given_tn,
                     p_score, with_scores=with_scores, with_roc=with_roc,
                     with_precision_recall=with_precision_recall, fnum=fnum)

        pt.adjust_subplots(hspace=.3, bottom=.05, left=.05)

        if figtitle is not None:
            pt.set_figtitle(figtitle)
        else:
            pt.set_figtitle('ScoreNorm test' + ut.dict_str(normkw, newlines=False))

        #confusions = get_confusion_metrics()
    locals_ = locals()
    return locals_


if __name__ == '__main__':
    """
    CommandLine:
        python -m vtool.score_normalization
        python -m vtool.score_normalization --allexamples
        python -m vtool.score_normalization --allexamples --noface --nosrc
    """
    import multiprocessing
    multiprocessing.freeze_support()  # for win32
    import utool as ut  # NOQA
    ut.doctest_funcs()