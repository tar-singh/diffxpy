import abc
import logging
from typing import Union, Dict, Tuple, List, Set, Callable
import pandas as pd
import warnings

import numpy as np
import scipy.sparse
import xarray as xr
import patsy
try:
    import anndata
except ImportError:
    anndata = None

import batchglm.data as data_utils
from batchglm.xarray_sparse import SparseXArrayDataArray, SparseXArrayDataSet
from batchglm.models.glm_nb import Model as GeneralizedLinearModel

from ..stats import stats
from . import correction
from ..models.batch_bfgs.optim import Estim_BFGS
from diffxpy import pkg_constants

logger = logging.getLogger(__name__)


def _dmat_unique(dmat, sample_description):
    dmat, idx = np.unique(dmat, axis=0, return_index=True)
    sample_description = sample_description.iloc[idx].reset_index(drop=True)

    return dmat, sample_description


class _Estimation(GeneralizedLinearModel, metaclass=abc.ABCMeta):
    """
    Dummy class specifying all needed methods / parameters necessary for a model
    fitted for DifferentialExpressionTest.
    Useful for type hinting.
    """

    @property
    @abc.abstractmethod
    def X(self) -> np.ndarray:
        pass

    @property
    @abc.abstractmethod
    def design_loc(self) -> np.ndarray:
        pass

    @property
    @abc.abstractmethod
    def design_scale(self) -> np.ndarray:
        pass

    @property
    @abc.abstractmethod
    def constraints_loc(self) -> np.ndarray:
        pass

    @property
    @abc.abstractmethod
    def constraints_scale(self) -> np.ndarray:
        pass

    @property
    @abc.abstractmethod
    def num_observations(self) -> int:
        pass

    @property
    @abc.abstractmethod
    def num_features(self) -> int:
        pass

    @property
    @abc.abstractmethod
    def features(self) -> np.ndarray:
        pass

    @property
    @abc.abstractmethod
    def observations(self) -> np.ndarray:
        pass

    @property
    @abc.abstractmethod
    def log_likelihood(self, **kwargs) -> np.ndarray:
        pass

    @property
    @abc.abstractmethod
    def loss(self, **kwargs) -> np.ndarray:
        pass

    @property
    @abc.abstractmethod
    def gradients(self, **kwargs) -> np.ndarray:
        pass

    @property
    @abc.abstractmethod
    def hessians(self, **kwargs) -> np.ndarray:
        pass

    @property
    @abc.abstractmethod
    def fisher_inv(self, **kwargs) -> np.ndarray:
        pass


class _DifferentialExpressionTest(metaclass=abc.ABCMeta):
    """
    Dummy class specifying all needed methods / parameters necessary for DifferentialExpressionTest.
    Useful for type hinting. Structure:
    Methods which are called by constructor and which compute (corrected) p-values:
        _test()
        _correction()
    Accessor methods for important metrics which have to be extracted from estimated models:
        log_fold_change()
        reduced_model_gradient()
        full_model_gradient()
    Interface method which provides summary of results:
        results()
        plot()
    """

    def __init__(self):
        self._pval = None
        self._qval = None
        self._mean = None
        self._log_likelihood = None

    @property
    @abc.abstractmethod
    def gene_ids(self) -> np.ndarray:
        pass

    @property
    @abc.abstractmethod
    def X(self):
        pass

    @abc.abstractmethod
    def log_fold_change(self, base=np.e, **kwargs):
        pass

    def log2_fold_change(self, **kwargs):
        """
        Calculates the pairwise log_2 fold change(s) for this DifferentialExpressionTest.
        """
        return self.log_fold_change(base=2, **kwargs)

    def log10_fold_change(self, **kwargs):
        """
        Calculates the log_10 fold change(s) for this DifferentialExpressionTest.
        """
        return self.log_fold_change(base=10, **kwargs)

    def _test(self, **kwargs) -> np.ndarray:
        pass

    def _correction(self, method) -> np.ndarray:
        """
        Performs multiple testing corrections available in statsmodels.stats.multitest.multipletests()
        on self.pval.

        :param method: Multiple testing correction method.
            Browse available methods in the annotation of statsmodels.stats.multitest.multipletests().
        """
        if np.all(np.isnan(self.pval)):
            return self.pval
        else:
            return correction.correct(pvals=self.pval, method=method)

    def _ave(self):
        """
        Returns a xr.DataArray containing the mean expression by gene

        :return: xr.DataArray
        """
        pass

    @property
    def log_likelihood(self):
        if self._log_likelihood is None:
            self._log_likelihood = self._ll().compute()
        return self._log_likelihood

    @property
    def mean(self):
        if self._mean is None:
            self._mean = self._ave()
            if isinstance(self._mean, xr.DataArray):  # Could also be np.ndarray coming out of XArraySparseDataArray
                self._mean = self._mean.compute()
        return self._mean

    @property
    def pval(self):
        if self._pval is None:
            self._pval = self._test().copy()
        return self._pval

    @property
    def qval(self, method="fdr_bh"):
        if self._qval is None:
            self._qval = self._correction(method=method).copy()
        return self._qval

    def log10_pval_clean(self, log10_threshold=-30):
        """
        Return log10 transformed and cleaned p-values.

        NaN p-values are set to one and p-values below log10_threshold
        in log10 space are set to log10_threshold.

        :param log10_threshold: minimal log10 p-value to return.
        :return: Cleaned log10 transformed p-values.
        """
        pvals = np.reshape(self.pval, -1)
        pvals = np.nextafter(0, 1, out=pvals, where=pvals == 0)
        log10_pval_clean = np.log(pvals) / np.log(10)
        log10_pval_clean[np.isnan(log10_pval_clean)] = 1
        log10_pval_clean = np.clip(log10_pval_clean, log10_threshold, 0, log10_pval_clean)
        return log10_pval_clean

    def log10_qval_clean(self, log10_threshold=-30):
        """
        Return log10 transformed and cleaned q-values.

        NaN p-values are set to one and q-values below log10_threshold
        in log10 space are set to log10_threshold.

        :param log10_threshold: minimal log10 q-value to return.
        :return: Cleaned log10 transformed q-values.
        """
        qvals = np.reshape(self.qval, -1)
        qvals = np.nextafter(0, 1, out=qvals, where=qvals == 0)
        log10_qval_clean = np.log(qvals) / np.log(10)
        log10_qval_clean[np.isnan(log10_qval_clean)] = 1
        log10_qval_clean = np.clip(log10_qval_clean, log10_threshold, 0, log10_qval_clean)
        return log10_qval_clean

    @abc.abstractmethod
    def summary(self, **kwargs) -> pd.DataFrame:
        pass

    def _threshold_summary(self, res, qval_thres=None,
                           fc_upper_thres=None, fc_lower_thres=None, mean_thres=None) -> pd.DataFrame:
        """
        Reduce differential expression results into an output table with desired thresholds.
        """
        if qval_thres is not None:
            res = res.iloc[res['qval'].values <= qval_thres, :]

        if fc_upper_thres is not None and fc_lower_thres is None:
            res = res.iloc[res['log2fc'].values >= np.log(fc_upper_thres) / np.log(2), :]
        elif fc_upper_thres is None and fc_lower_thres is not None:
            res = res.iloc[res['log2fc'].values <= np.log(fc_lower_thres) / np.log(2), :]
        elif fc_upper_thres is not None and fc_lower_thres is not None:
            res = res.iloc[np.logical_or(
                res['log2fc'].values <= np.log(fc_lower_thres) / np.log(2),
                res['log2fc'].values >= np.log(fc_upper_thres) / np.log(2)), :]

        if mean_thres is not None:
            res = res.iloc[res['mean'].values >= mean_thres, :]

        return res

    def plot_volcano(
            self,
            corrected_pval=True,
            log10_p_threshold=-30,
            log2_fc_threshold=10,
            alpha=0.05,
            min_fc=1,
            size=20,
            highlight_ids: List = [],
            highlight_size: float = 30,
            highlight_col: str = "red",
            show: bool = True,
            save: Union[str, None] = None,
            suffix: str = "_volcano.png"
    ):
        """
        Returns a volcano plot of p-value vs. log fold change

        :param corrected_pval: Whether to use multiple testing corrected
            or raw p-values.
        :param log10_p_threshold: lower bound of log10 p-values displayed in plot.
        :param log2_fc_threshold: Negative lower and upper bound of
            log2 fold change displayed in plot.
        :param alpha: p/q-value lower bound at which a test is considered
            non-significant. The corresponding points are colored in grey.
        :param min_fc: Fold-change lower bound for visualization,
            the points below the threshold are colored in grey.
        :param size: Size of points.
        :param highlight_ids: Genes to highlight in volcano plot.
        :param highlight_ids: Size of points of genes to highlight in volcano plot.
        :param highlight_ids: Color of points of genes to highlight in volcano plot.
        :param show: Whether (if save is not None) and where (save indicates dir and file stem) to display plot.
        :param save: Path+file name stem to save plots to.
            File will be save+suffix. Does not save if save is None.
        :param suffix: Suffix for file name to save plot to. Also use this to set the file type.

        :return: Tuple of matplotlib (figure, axis)
        """
        import seaborn as sns
        import matplotlib.pyplot as plt
        from matplotlib import gridspec
        from matplotlib import rcParams

        plt.ioff()

        if corrected_pval == True:
            neg_log_pvals = - self.log10_qval_clean(log10_threshold=log10_p_threshold)
        else:
            neg_log_pvals = - self.log10_pval_clean(log10_threshold=log10_p_threshold)

        logfc = np.reshape(self.log2_fold_change(), -1)
        # Clipping throws errors if not performed in actual data format (ndarray or DataArray):
        if isinstance(logfc, xr.DataArray):
            logfc = logfc.clip(-log2_fc_threshold, log2_fc_threshold)
        else:
            logfc = np.clip(logfc, -log2_fc_threshold, log2_fc_threshold, logfc)

        fig, ax = plt.subplots()

        is_significant = np.logical_and(
            neg_log_pvals >= - np.log(alpha) / np.log(10),
            np.abs(logfc) >= np.log(min_fc) / np.log(2)
        )

        sns.scatterplot(y=neg_log_pvals, x=logfc, hue=is_significant, ax=ax,
                        legend=False, s=size,
                        palette={True: "orange", False: "black"})

        highlight_ids_found = np.array([x in self.gene_ids for x in highlight_ids])
        highlight_ids_clean = [highlight_ids[i] for i in np.where(highlight_ids_found == True)[0]]
        highlight_ids_not_found = [highlight_ids[i] for i in np.where(highlight_ids_found == False)[0]]
        if len(highlight_ids_not_found) > 0:
            logger.warning("not all highlight_ids were found in data set: ", ", ".join(highlight_ids_not_found))

        if len(highlight_ids_clean) > 0:
            neg_log_pvals_highlights = np.zeros([len(highlight_ids_clean)])
            logfc_highlights = np.zeros([len(highlight_ids_clean)])
            is_highlight = np.zeros([len(highlight_ids_clean)])
            for i,id in enumerate(highlight_ids_clean):
                idx = np.where(self.gene_ids == id)[0]
                neg_log_pvals_highlights[i] = neg_log_pvals[idx]
                logfc_highlights[i] = logfc[idx]

            sns.scatterplot(y=neg_log_pvals_highlights, x=logfc_highlights,
                            hue=is_highlight, ax=ax,
                            legend=False, s=highlight_size,
                            palette={0: highlight_col})


        if corrected_pval == True:
            ax.set(xlabel="log2FC", ylabel='-log10(corrected p-value)')
        else:
            ax.set(xlabel="log2FC", ylabel='-log10(p-value)')

        # Save, show and return figure.
        if save is not None:
            plt.savefig(save + suffix)

        if show:
            plt.show()

        plt.close(fig)

        return ax

    def plot_ma(
            self,
            corrected_pval=True,
            log2_fc_threshold=10,
            alpha=0.05,
            size=20,
            highlight_ids: List = [],
            highlight_size: float = 30,
            highlight_col: str = "red",
            show: bool = True,
            save: Union[str, None] = None,
            suffix: str = "_my_plot.png"
    ):
        """
        Returns an MA plot of mean expression vs. log fold change with significance
        super-imposed.

        :param corrected_pval: Whether to use multiple testing corrected
            or raw p-values.
        :param log2_fc_threshold: Negative lower and upper bound of
            log2 fold change displayed in plot.
        :param alpha: p/q-value lower bound at which a test is considered
            non-significant. The corresponding points are colored in grey.
        :param size: Size of points.
        :param highlight_ids: Genes to highlight in volcano plot.
        :param highlight_ids: Size of points of genes to highlight in volcano plot.
        :param highlight_ids: Color of points of genes to highlight in volcano plot.
        :param show: Whether (if save is not None) and where (save indicates dir and file stem) to display plot.
        :param save: Path+file name stem to save plots to.
            File will be save+suffix. Does not save if save is None.
        :param suffix: Suffix for file name to save plot to. Also use this to set the file type.


        :return: Tuple of matplotlib (figure, axis)
        """
        import seaborn as sns
        import matplotlib.pyplot as plt
        from matplotlib import gridspec
        from matplotlib import rcParams

        plt.ioff()

        ave = np.log(self.mean + 1e-08)

        logfc = np.reshape(self.log2_fold_change(), -1)
        # Clipping throws errors if not performed in actual data format (ndarray or DataArray):
        if isinstance(logfc, xr.DataArray):
            logfc = logfc.clip(-log2_fc_threshold, log2_fc_threshold)
        else:
            logfc = np.clip(logfc, -log2_fc_threshold, log2_fc_threshold, logfc)

        fig, ax = plt.subplots()

        if corrected_pval:
            is_significant = self.pval < alpha
        else:
            is_significant = self.qval < alpha

        sns.scatterplot(y=logfc, x=ave, hue=is_significant, ax=ax,
                        legend=False, s=size,
                        palette={True: "orange", False: "black"})

        highlight_ids_found = np.array([x in self.gene_ids for x in highlight_ids])
        highlight_ids_clean = [highlight_ids[i] for i in np.where(highlight_ids_found == True)[0]]
        highlight_ids_not_found = [highlight_ids[i] for i in np.where(highlight_ids_found == False)[0]]
        if len(highlight_ids_not_found) > 0:
            logger.warning("not all highlight_ids were found in data set: ", ", ".join(highlight_ids_not_found))

        if len(highlight_ids_clean) > 0:
            ave_highlights = np.zeros([len(highlight_ids_clean)])
            logfc_highlights = np.zeros([len(highlight_ids_clean)])
            is_highlight = np.zeros([len(highlight_ids_clean)])
            for i,id in enumerate(highlight_ids_clean):
                idx = np.where(self.gene_ids == id)[0]
                ave_highlights[i] = ave[idx]
                logfc_highlights[i] = logfc[idx]

            sns.scatterplot(y=logfc_highlights, x=ave_highlights,
                            hue=is_highlight, ax=ax,
                            legend=False, s=highlight_size,
                            palette={0: highlight_col})

        ax.set(xlabel="log2FC", ylabel='log mean expression')

        # Save, show and return figure.
        if save is not None:
            plt.savefig(save + suffix)

        if show:
            plt.show()

        plt.close(fig)

        return ax

    def plot_diagnostics(self):
        """
        Directly plots a set of diagnostic diagrams
        """
        import matplotlib.pyplot as plt

        volcano = self.plot_volcano()
        plt.show()


class _DifferentialExpressionTestSingle(_DifferentialExpressionTest, metaclass=abc.ABCMeta):
    """
    _DifferentialExpressionTest for unit_test with a single test per gene.
    The individual test object inherit directly from this class.

    All implementations of this class should return one p-value and one fold change per gene.
    """

    def summary(
            self,
            qval_thres=None,
            fc_upper_thres=None,
            fc_lower_thres=None,
            mean_thres=None,
            **kwargs
    ) -> pd.DataFrame:
        """
        Summarize differential expression results into an output table.
        """
        assert self.gene_ids is not None

        res = pd.DataFrame({
            "gene": self.gene_ids,
            "pval": self.pval,
            "qval": self.qval,
            "log2fc": self.log2_fold_change(),
            "mean": self.mean
        })

        return res


class DifferentialExpressionTestLRT(_DifferentialExpressionTestSingle):
    """
    Single log-likelihood ratio test per gene.
    """

    sample_description: pd.DataFrame
    full_design_loc_info: patsy.design_info
    full_estim: _Estimation
    reduced_design_loc_info: patsy.design_info
    reduced_estim: _Estimation

    def __init__(
            self,
            sample_description: pd.DataFrame,
            full_design_loc_info: patsy.design_info,
            full_estim,
            reduced_design_loc_info: patsy.design_info,
            reduced_estim
    ):
        super().__init__()
        self.sample_description = sample_description
        self.full_design_loc_info = full_design_loc_info
        self.full_estim = full_estim
        self.reduced_design_loc_info = reduced_design_loc_info
        self.reduced_estim = reduced_estim

    @property
    def gene_ids(self) -> np.ndarray:
        return np.asarray(self.full_estim.features)

    @property
    def X(self):
        return self.full_estim.X

    @property
    def reduced_model_gradient(self):
        return self.reduced_estim.gradients

    @property
    def full_model_gradient(self):
        return self.full_estim.gradients

    def _test(self):
        if np.any(self.full_estim.log_likelihood < self.reduced_estim.log_likelihood):
            logger.warning("Test assumption failed: full model is (partially) less probable than reduced model")

        return stats.likelihood_ratio_test(
            ll_full=self.full_estim.log_likelihood,
            ll_reduced=self.reduced_estim.log_likelihood,
            df_full=self.full_estim.constraints_loc.shape[1] + self.full_estim.constraints_scale.shape[1],
            df_reduced=self.reduced_estim.constraints_loc.shape[1] + self.reduced_estim.constraints_scale.shape[1],
        )

    def _ave(self):
        """
        Returns a xr.DataArray containing the mean expression by gene

        :return: xr.DataArray
        """

        return np.mean(self.full_estim.X, axis=0)

    def _log_fold_change(self, factors: Union[Dict, Tuple, Set, List], base=np.e):
        """
        Returns a xr.DataArray containing the locations for the different categories of the factors

        :param factors: the factors to select.
            E.g. `condition` or `batch` if formula would be `~ 1 + batch + condition`
        :param base: the log base to use; default is the natural logarithm
        :return: xr.DataArray
        """

        if not (isinstance(factors, list) or isinstance(factors, tuple) or isinstance(factors, set)):
            factors = {factors}
        if not isinstance(factors, set):
            factors = set(factors)

        di = self.full_design_loc_info
        sample_description = self.sample_description[[f.name() for f in di.subset(factors).factor_infos]]
        dmat = self.full_estim.design_loc

        # make rows unique
        dmat, sample_description = _dmat_unique(dmat, sample_description)

        # factors = factors.intersection(di.term_names)

        # select the columns of the factors
        cols = np.arange(len(di.column_names))
        sel = np.concatenate([cols[di.slice(f)] for f in factors], axis=0)
        neg_sel = np.ones_like(cols).astype(bool)
        neg_sel[sel] = False

        # overwrite all columns which are not specified by the factors with 0
        dmat[:, neg_sel] = 0

        # make the design matrix + sample description unique again
        dmat, sample_description = _dmat_unique(dmat, sample_description)

        locations = self.full_estim.inverse_link_loc(dmat.dot(self.full_estim.par_link_loc))
        locations = np.log(locations) / np.log(base)

        dist = np.expand_dims(locations, axis=0)
        dist = np.transpose(dist, [1, 0, 2]) - dist
        dist = xr.DataArray(dist, dims=("minuend", "subtrahend", "gene"))
        # retval = xr.Dataset({"logFC": retval})

        dist.coords["gene"] = self.gene_ids

        for col in sample_description:
            dist.coords["minuend_" + col] = (("minuend",), sample_description[col])
            dist.coords["subtrahend_" + col] = (("subtrahend",), sample_description[col])

        # # If this is a pairwise comparison, return only one fold change per gene
        # if dist.shape[:2] == (2, 2):
        #     dist = dist[1, 0]

        return dist

    def log_fold_change(self, base=np.e, return_type="vector"):
        """
        Calculates the pairwise log fold change(s) for this DifferentialExpressionTest.
        Returns some distance matrix representation of size (groups x groups x genes) where groups corresponds
        to the unique groups compared in this differential expression test.

        :param base: the log base to use; default is the natural logarithm
        :param return_type: Choose the return type.
            Possible values are:

            - "dataframe":
              return a pandas.DataFrame with columns `gene`, `minuend_<group>`, `subtrahend_<group>` and `logFC`.
            - "xarray":
              return a xarray.DataArray with dimensions `(minuend, subtrahend, gene)`

        :return: either pandas.DataFrame or xarray.DataArray
        """
        factors = set(self.full_design_loc_info.term_names) - set(self.reduced_design_loc_info.term_names)

        if return_type == "dataframe":
            dists = self._log_fold_change(factors=factors, base=base)

            df = dists.to_dataframe("logFC")
            df = df.reset_index().drop(["minuend", "subtrahend"], axis=1, errors="ignore")
            return df
        elif return_type == "vector":
            if len(factors) > 1 or self.sample_description[list(factors)].drop_duplicates().shape[0] != 2:
                return None
            else:
                dists = self._log_fold_change(factors=factors, base=base)
                return dists[1, 0].values
        else:
            dists = self._log_fold_change(factors=factors, base=base)
            return dists

    def locations(self):
        """
        Returns a pandas.DataFrame containing the locations for the different categories of the factors

        :return: pd.DataFrame
        """

        di = self.full_design_loc_info
        sample_description = self.sample_description[[f.name() for f in di.factor_infos]]
        dmat = self.full_estim.design_loc

        dmat, sample_description = _dmat_unique(dmat, sample_description)

        retval = self.full_estim.inverse_link_loc(dmat.dot(self.full_estim.par_link_loc))
        retval = pd.DataFrame(retval, columns=self.full_estim.features)
        for col in sample_description:
            retval[col] = sample_description[col]

        retval = retval.set_index(list(sample_description.columns))

        return retval

    def scales(self):
        """
        Returns a pandas.DataFrame containing the scales for the different categories of the factors

        :return: pd.DataFrame
        """

        di = self.full_design_loc_info
        sample_description = self.sample_description[[f.name() for f in di.factor_infos]]
        dmat = self.full_estim.design_scale

        dmat, sample_description = _dmat_unique(dmat, sample_description)

        retval = self.full_estim.inverse_link_scale(dmat.doc(self.full_estim.par_link_scale))
        retval = pd.DataFrame(retval, columns=self.full_estim.features)
        for col in sample_description:
            retval[col] = sample_description[col]

        retval = retval.set_index(list(sample_description.columns))

        return retval

    def summary(self, qval_thres=None, fc_upper_thres=None,
                fc_lower_thres=None, mean_thres=None,
                **kwargs) -> pd.DataFrame:
        """
        Summarize differential expression results into an output table.
        """
        res = super().summary(**kwargs)
        res["grad"] = self.full_model_gradient.data
        res["grad_red"] = self.reduced_model_gradient.data

        res = self._threshold_summary(
            res=res,
            qval_thres=qval_thres,
            fc_upper_thres=fc_upper_thres,
            fc_lower_thres=fc_lower_thres,
            mean_thres=mean_thres
        )

        return res


class DifferentialExpressionTestWald(_DifferentialExpressionTestSingle):
    """
    Single wald test per gene.
    """

    model_estim: _Estimation
    coef_loc_totest: np.ndarray
    theta_mle: np.ndarray
    theta_sd: np.ndarray
    _error_codes: np.ndarray
    _niter: np.ndarray

    def __init__(
            self,
            model_estim: _Estimation,
            col_indices: np.ndarray
    ):
        """
        :param model_estim:
        :param cold_index: indices of coefs to test
        """
        super().__init__()

        self.model_estim = model_estim
        self.coef_loc_totest = col_indices

        try:
            if model_estim._error_codes is not None:
                self._error_codes = model_estim._error_codes
        except Exception as e:
            self._error_codes = None

        try:
            if model_estim._niter is not None:
                self._niter = model_estim._niter
        except Exception as e:
            self._niter = None

    @property
    def gene_ids(self) -> np.ndarray:
        return np.asarray(self.model_estim.features)

    @property
    def X(self):
        return self.model_estim.X

    @property
    def model_gradient(self):
        return self.model_estim.gradients

    def log_fold_change(self, base=np.e, **kwargs):
        """
        Returns one fold change per gene

        Returns coefficient if only one coefficient is testeed.
        Returns mean absolute coefficient if multiple coefficients are tested.
        """
        # design = np.unique(self.model_estim.design_loc, axis=0)
        # dmat = np.zeros_like(design)
        # dmat[:, self.coef_loc_totest] = design[:, self.coef_loc_totest]

        # loc = dmat @ self.model_estim.par_link_loc[self.coef_loc_totest]
        # return loc[1] - loc[0]
        if len(self.coef_loc_totest) == 1:
            return self.model_estim.a_var[self.coef_loc_totest][0]
        else:
            idx_max = np.argmax(np.abs(self.model_estim.a_var[self.coef_loc_totest]), axis=0)
            return self.model_estim.a_var[self.coef_loc_totest][
                idx_max, np.arange(self.model_estim.a_var.shape[1])]

    def _ll(self):
        """
        Returns a xr.DataArray containing the log likelihood of each gene

        :return: xr.DataArray
        """
        return self.model_estim.log_likelihood

    def _ave(self):
        """
        Returns a xr.DataArray containing the mean expression by gene

        :return: xr.DataArray
        """
        return self.X.mean(axis=0)

    def _test(self):
        """
        Returns a xr.DataArray containing the p-value for differential expression for each gene

        :return: xr.DataArray
        """
        # Check whether single- or multiple parameters are tested.
        # For a single parameter, the wald statistic distribution is approximated
        # with a normal distribution, for multiple parameters, a chi-square distribution is used.
        self.theta_mle = self.model_estim.a_var[self.coef_loc_totest]
        if len(self.coef_loc_totest) == 1:
            self.theta_mle = self.theta_mle[0]  # Make xarray one dimensional for stats.wald_test.
            self.theta_sd = self.model_estim.fisher_inv[:, self.coef_loc_totest[0], self.coef_loc_totest[0]].values
            self.theta_sd = np.nextafter(0, np.inf, out=self.theta_sd,
                                         where=self.theta_sd < np.nextafter(0, np.inf))
            self.theta_sd = np.sqrt(self.theta_sd)
            return stats.wald_test(
                theta_mle=self.theta_mle,
                theta_sd=self.theta_sd,
                theta0=0
            )
        else:
            self.theta_sd = np.diagonal(self.model_estim.fisher_inv, axis1=-2, axis2=-1).copy()
            self.theta_sd = np.nextafter(0, np.inf, out=self.theta_sd,
                                         where=self.theta_sd < np.nextafter(0, np.inf))
            self.theta_sd = np.sqrt(self.theta_sd)
            return stats.wald_test_chisq(
                theta_mle=self.theta_mle,
                theta_covar=self.model_estim.fisher_inv[:, self.coef_loc_totest, self.coef_loc_totest],
                theta0=0
            )

    def summary(self, qval_thres=None, fc_upper_thres=None,
                fc_lower_thres=None, mean_thres=None,
                **kwargs) -> pd.DataFrame:
        """
        Summarize differential expression results into an output table.
        """
        res = super().summary(**kwargs)
        res["grad"] = self.model_gradient.data
        if len(self.theta_mle.shape) == 1:
            res["coef_mle"] = self.theta_mle
        if len(self.theta_sd.shape) == 1:
            res["coef_sd"] = self.theta_sd
        # add in info from bfgs
        if self.log_likelihood is not None:
            res["ll"] = self.log_likelihood
        if self._error_codes is not None:
            res["err"] = self._error_codes
        if self._niter is not None:
            res["niter"] = self._niter

        res = self._threshold_summary(
            res=res,
            qval_thres=qval_thres,
            fc_upper_thres=fc_upper_thres,
            fc_lower_thres=fc_lower_thres,
            mean_thres=mean_thres
        )

        return res

    def plot_vs_ttest(self):
        import matplotlib.pyplot as plt
        import seaborn as sns

        grouping = np.asarray(self.model_estim.design_loc[:, self.coef_loc_totest])
        ttest = t_test(
            data=self.model_estim.X,
            grouping=grouping,
            gene_names=self.gene_ids,
        )
        ttest_pvals = ttest.pval

        fig, ax = plt.subplots()

        sns.scatterplot(x=ttest_pvals, y=self.pval, ax=ax)

        ax.set(xlabel="t-test", ylabel='wald test')

        return fig, ax

    def plot_diagnostics(self):
        import matplotlib.pyplot as plt

        volcano = self.plot_volcano()
        plt.show()
        ttest_comp = self.plot_vs_ttest()
        plt.show()


class DifferentialExpressionTestTT(_DifferentialExpressionTestSingle):
    """
    Single t-test test per gene.
    """

    def __init__(self, data, grouping, gene_names):
        super().__init__()
        self._X = data
        self.grouping = grouping
        self._gene_names = np.asarray(gene_names)

        x0, x1 = _split_X(data, grouping)

        # Only compute p-values for genes with non-zero observations and non-zero group-wise variance.
        mean_x0 = x0.mean(axis=0)
        mean_x1 = x1.mean(axis=0)
        mean_x0 = mean_x0.clip(np.nextafter(0, 1), np.inf)
        mean_x1 = mean_x1.clip(np.nextafter(0, 1), np.inf)
        # TODO: do not need mean again
        self._mean = data.mean(axis=0)
        self._ave_geq_zero = np.asarray(self.mean).flatten() > 0
        var_x0 = np.asarray(x0.var(axis=0)).flatten()
        var_x1 = np.asarray(x1.var(axis=0)).flatten()
        self._var_geq_zero = np.logical_or(
            var_x0 > 0,
            var_x1 > 0
        )
        idx_run = np.where(np.logical_and(self._ave_geq_zero == True, self._var_geq_zero == True))[0]
        pval = np.zeros([data.shape[1]]) + np.nan
        pval[idx_run] = stats.t_test_moments(
            mu0=mean_x0[idx_run],
            mu1=mean_x1[idx_run],
            var0=var_x0[idx_run],
            var1=var_x1[idx_run],
            n0=idx_run.shape[0],
            n1=idx_run.shape[0]
        )
        self._pval = pval

        self._logfc = np.log(mean_x1) - np.log(mean_x0).data
        # Return 0 if LFC was non-zero and variances are zero,
        # this causes division by zero in the test statistic. This
        # is a highly significant result if one believes the variance estimate.
        pval[np.logical_and(np.logical_and(self._var_geq_zero == False,
                                           self._ave_geq_zero == True),
                            self._logfc != 0)] = 0
        q = self.qval

    @property
    def gene_ids(self) -> np.ndarray:
        return self._gene_names

    @property
    def X(self):
        return self._X

    def log_fold_change(self, base=np.e, **kwargs):
        """
        Returns one fold change per gene
        """
        if base == np.e:
            return self._logfc
        else:
            return self._logfc / np.log(base)

    def summary(self, qval_thres=None, fc_upper_thres=None,
                fc_lower_thres=None, mean_thres=None,
                **kwargs) -> pd.DataFrame:
        """
        Summarize differential expression results into an output table.
        """
        res = super().summary(**kwargs)
        res["zero_mean"] = self._ave_geq_zero == False
        res["zero_variance"] = self._var_geq_zero == False

        res = self._threshold_summary(
            res=res,
            qval_thres=qval_thres,
            fc_upper_thres=fc_upper_thres,
            fc_lower_thres=fc_lower_thres,
            mean_thres=mean_thres
        )

        return res


class DifferentialExpressionTestRank(_DifferentialExpressionTestSingle):
    """
    Single rank test per gene (Mann-Whitney U test).
    """

    def __init__(self, data, grouping, gene_names):
        super().__init__()
        self._X = data
        self.grouping = grouping
        self._gene_names = np.asarray(gene_names)

        x0, x1 = _split_X(data, grouping)

        mean_x0 = x0.mean(axis=0)
        mean_x1 = x1.mean(axis=0)
        mean_x0 = mean_x0.clip(np.nextafter(0, 1), np.inf)
        mean_x1 = mean_x1.clip(np.nextafter(0, 1), np.inf)
        # TODO unnecessary mean computation
        self._mean = data.mean(axis=0)
        var_x0 = np.asarray(x0.var(axis=0)).flatten()
        var_x1 = np.asarray(x1.var(axis=0)).flatten()
        self._var_geq_zero = np.logical_or(
            var_x0 > 0,
            var_x1 > 0
        )
        idx_run = np.where(np.logical_and(self._mean > 0, self._var_geq_zero == True))[0]

        # TODO: can this be done on sparse?
        pval = np.zeros([data.shape[1]]) + np.nan
        if isinstance(x0, xr.DataArray):
            pval[idx_run] = stats.mann_whitney_u_test(
                x0=x0.data[:,idx_run],
                x1=x1.data[:,idx_run]
            )
        else:
            pval[idx_run] = stats.mann_whitney_u_test(
                x0=np.asarray(x0.X[:,idx_run].todense()),
                x1=np.asarray(x1.X[:,idx_run].todense())
            )

        self._pval = pval

        self._logfc = np.log(mean_x1) - np.log(mean_x0).data
        q = self.qval

    @property
    def gene_ids(self) -> np.ndarray:
        return self._gene_names

    @property
    def X(self):
        return self._X

    def log_fold_change(self, base=np.e, **kwargs):
        """
        Returns one fold change per gene
        """
        if base == np.e:
            return self._logfc
        else:
            return self._logfc / np.log(base)

    def summary(self, qval_thres=None, fc_upper_thres=None,
                fc_lower_thres=None, mean_thres=None,
                **kwargs) -> pd.DataFrame:
        """
        Summarize differential expression results into an output table.
        """
        res = super().summary(**kwargs)

        res = self._threshold_summary(
            res=res,
            qval_thres=qval_thres,
            fc_upper_thres=fc_upper_thres,
            fc_lower_thres=fc_lower_thres,
            mean_thres=mean_thres
        )

        return res

    def plot_vs_ttest(self):
        import matplotlib.pyplot as plt
        import seaborn as sns

        grouping = self.grouping
        ttest = t_test(
            data=self.X,
            grouping=grouping,
            gene_names=self.gene_ids,
        )
        ttest_pvals = ttest.pval

        fig, ax = plt.subplots()

        sns.scatterplot(x=ttest_pvals, y=self.pval, ax=ax)

        ax.set(xlabel="t-test", ylabel='rank test')

        return fig, ax

    def plot_diagnostics(self):
        import matplotlib.pyplot as plt

        volcano = self.plot_volcano()
        plt.show()
        ttest_comp = self.plot_vs_ttest()
        plt.show()


class _DifferentialExpressionTestMulti(_DifferentialExpressionTest, metaclass=abc.ABCMeta):
    """
    _DifferentialExpressionTest for unit_test with a multiple unit_test per gene.
    The individual test object inherit directly from this class.
    """

    def __init__(self, correction_type: str):
        """

        :param correction_type: Choose between global and test-wise correction.
            Can be:

            - "global": correct all p-values in one operation
            - "by_test": correct the p-values of each test individually
        """
        super().__init__()
        self._correction_type = correction_type

    def _correction(self, method):
        if self._correction_type.lower() == "global":
            pvals = np.reshape(self.pval, -1)
            qvals = correction.correct(pvals=pvals, method=method)
            qvals = np.reshape(qvals, self.pval.shape)
            return qvals
        elif self._correction_type.lower() == "by_test":
            qvals = np.apply_along_axis(
                func1d=lambda pvals: correction.correct(pvals=pvals, method=method),
                axis=-1,
                arr=self.pval,
            )
            return qvals

    def summary(self, **kwargs) -> pd.DataFrame:
        """
        Summarize differential expression results into an output table.

        :return: pandas.DataFrame with the following columns:

            - gene: the gene id's
            - pval: the minimum per-gene p-value of all tests
            - qval: the minimum per-gene q-value of all tests
            - log2fc: the maximal/minimal (depending on which one is higher) log2 fold change of the genes
            - mean: the mean expression of the gene across all groups
        """
        assert self.gene_ids is not None

        # calculate maximum logFC of lower triangular fold change matrix
        raw_logfc = self.log2_fold_change()

        # first flatten all dimensions up to the last 'gene' dimension
        flat_logfc = raw_logfc.reshape(-1, raw_logfc.shape[-1])
        # next, get argmax of flattened logfc and unravel the true indices from it
        r, c = np.unravel_index(flat_logfc.argmax(0), raw_logfc.shape[:2])
        # if logfc is maximal in the lower triangular matrix, multiply it with -1
        logfc = raw_logfc[r, c, np.arange(raw_logfc.shape[-1])] * np.where(r <= c, 1, -1)

        res = pd.DataFrame({
            "gene": self.gene_ids,
            # return minimal pval by gene:
            "pval": np.min(self.pval.reshape(-1, self.pval.shape[-1]), axis=0),
            # return minimal qval by gene:
            "qval": np.min(self.qval.reshape(-1, self.qval.shape[-1]), axis=0),
            # return maximal logFC by gene:
            "log2fc": np.asarray(logfc),
            # return mean expression across all groups by gene:
            "mean": np.asarray(self.mean)
        })

        return res


class DifferentialExpressionTestPairwise(_DifferentialExpressionTestMulti):
    """
    Pairwise unit_test between more than 2 groups per gene.
    """

    def __init__(self, gene_ids, pval, logfc, ave, groups, tests, correction_type: str):
        super().__init__(correction_type=correction_type)
        self._gene_ids = np.asarray(gene_ids)
        self._logfc = logfc
        self._pval = pval
        self._mean = ave
        self.groups = list(np.asarray(groups))
        self._tests = tests

        q = self.qval

    @property
    def gene_ids(self) -> np.ndarray:
        return self._gene_ids

    @property
    def X(self):
        return None

    @property
    def tests(self):
        """
        If `keep_full_test_objs` was set to `True`, this will return a matrix of differential expression tests.
        """
        if self._tests is None:
            raise ValueError("Individual tests were not kept!")

        return self._tests

    def log_fold_change(self, base=np.e, **kwargs):
        """
        Returns matrix of fold changes per gene
        """
        if base == np.e:
            return self._logfc
        else:
            return self._logfc / np.log(base)

    def _check_groups(self, group1, group2):
        if group1 not in self.groups:
            raise ValueError('group1 not recognized')
        if group2 not in self.groups:
            raise ValueError('group2 not recognized')

    def pval_pair(self, group1, group2):
        """
        Get p-values of the comparison of group1 and group2.

        :param group1: Identifier of first group of observations in pair-wise comparison.
        :param group2: Identifier of second group of observations in pair-wise comparison.
        :return: p-values
        """
        assert self._pval is not None

        self._check_groups(group1, group2)
        return self._pval[self.groups.index(group1), self.groups.index(group2), :]

    def qval_pair(self, group1, group2):
        """
        Get q-values of the comparison of group1 and group2.

        :param group1: Identifier of first group of observations in pair-wise comparison.
        :param group2: Identifier of second group of observations in pair-wise comparison.
        :return: q-values
        """
        assert self._qval is not None

        self._check_groups(group1, group2)
        return self._qval[self.groups.index(group1), self.groups.index(group2), :]

    def log10_pval_pair_clean(self, group1, group2, log10_threshold=-30):
        """
        Return log10 transformed and cleaned p-values.

        NaN p-values are set to one and p-values below log10_threshold
        in log10 space are set to log10_threshold.

        :param group1: Identifier of first group of observations in pair-wise comparison.
        :param group2: Identifier of second group of observations in pair-wise comparison.
        :param log10_threshold: minimal log10 p-value to return.
        :return: Cleaned log10 transformed p-values.
        """
        pvals = np.reshape(self.pval_pair(group1=group1, group2=group2), -1)
        pvals = np.nextafter(0, 1, out=pvals, where=pvals == 0)
        log10_pval_clean = np.log(pvals) / np.log(10)
        log10_pval_clean[np.isnan(log10_pval_clean)] = 1
        log10_pval_clean = np.clip(log10_pval_clean, log10_threshold, 0, log10_pval_clean)
        return log10_pval_clean

    def log10_qval_pair_clean(self, group1, group2, log10_threshold=-30):
        """
        Return log10 transformed and cleaned q-values.

        NaN p-values are set to one and q-values below log10_threshold
        in log10 space are set to log10_threshold.

        :param group1: Identifier of first group of observations in pair-wise comparison.
        :param group2: Identifier of second group of observations in pair-wise comparison.
        :param log10_threshold: minimal log10 q-value to return.
        :return: Cleaned log10 transformed q-values.
        """
        qvals = np.reshape(self.qval_pair(group1=group1, group2=group2), -1)
        qvals = np.nextafter(0, 1, out=qvals, where=qvals == 0)
        log10_qval_clean = np.log(qvals) / np.log(10)
        log10_qval_clean[np.isnan(log10_qval_clean)] = 1
        log10_qval_clean = np.clip(log10_qval_clean, log10_threshold, 0, log10_qval_clean)
        return log10_qval_clean

    def log_fold_change_pair(self, group1, group2, base=np.e):
        """
        Get log fold changes of the comparison of group1 and group2.

        :param group1: Identifier of first group of observations in pair-wise comparison.
        :param group2: Identifier of second group of observations in pair-wise comparison.
        :return: log fold changes
        """
        assert self._logfc is not None

        self._check_groups(group1, group2)
        return self.log_fold_change(base=base)[self.groups.index(group1), self.groups.index(group2), :]

    def summary(self, qval_thres=None, fc_upper_thres=None,
                fc_lower_thres=None, mean_thres=None,
                **kwargs) -> pd.DataFrame:
        """
        Summarize differential expression results into an output table.
        """
        res = super().summary(**kwargs)

        res = self._threshold_summary(
            res=res,
            qval_thres=qval_thres,
            fc_upper_thres=fc_upper_thres,
            fc_lower_thres=fc_lower_thres,
            mean_thres=mean_thres
        )

        return res

    def summary_pair(self, group1, group2,
                     qval_thres=None, fc_upper_thres=None,
                     fc_lower_thres=None, mean_thres=None,
                     **kwargs) -> pd.DataFrame:
        """
        Summarize differential expression results into an output table.

        :param group1: Identifier of first group of observations in pair-wise comparison.
        :param group2: Identifier of second group of observations in pair-wise comparison.
        :return: pandas.DataFrame with the following columns:

            - gene: the gene id's
            - pval: the per-gene p-value of the selected test
            - qval: the per-gene q-value of the selected test
            - log2fc: the per-gene log2 fold change of the selected test
            - mean: the mean expression of the gene across all groups
        """
        assert self.gene_ids is not None

        res = pd.DataFrame({
            "gene": self.gene_ids,
            "pval": self.pval_pair(group1=group1, group2=group2),
            "qval": self.qval_pair(group1=group1, group2=group2),
            "log2fc": self.log_fold_change_pair(group1=group1, group2=group2, base=2),
            "mean": np.asarray(self.mean)
        })

        res = self._threshold_summary(
            res=res,
            qval_thres=qval_thres,
            fc_upper_thres=fc_upper_thres,
            fc_lower_thres=fc_lower_thres,
            mean_thres=mean_thres
        )

        return res


class DifferentialExpressionTestZTest(_DifferentialExpressionTestMulti):
    """
    Pairwise unit_test between more than 2 groups per gene.
    """

    model_estim: _Estimation
    theta_mle: np.ndarray
    theta_sd: np.ndarray

    def __init__(self, model_estim: _Estimation, grouping, groups, correction_type: str):
        super().__init__(correction_type=correction_type)
        self.model_estim = model_estim
        self.grouping = grouping
        self.groups = list(np.asarray(groups))

        # values of parameter estimates: coefficients x genes array with one coefficient per group
        self._theta_mle = model_estim.par_link_loc
        # standard deviation of estimates: coefficients x genes array with one coefficient per group
        # theta_sd = sqrt(diagonal(fisher_inv))
        self._theta_sd = np.sqrt(np.diagonal(model_estim.fisher_inv, axis1=-2, axis2=-1)).T
        self._logfc = None

        # Call tests in constructor.
        p = self.pval
        q = self.qval

    def _test(self, **kwargs):
        groups = self.groups
        num_features = self.model_estim.X.shape[1]

        pvals = np.tile(np.NaN, [len(groups), len(groups), num_features])
        pvals[np.eye(pvals.shape[0]).astype(bool)] = 1

        theta_mle = self._theta_mle
        theta_sd = self._theta_sd

        for i, g1 in enumerate(groups):
            for j, g2 in enumerate(groups[(i + 1):]):
                j = j + i + 1

                pvals[i, j] = stats.two_coef_z_test(theta_mle0=theta_mle[i], theta_mle1=theta_mle[j],
                                                    theta_sd0=theta_sd[i], theta_sd1=theta_sd[j])
                pvals[j, i] = pvals[i, j]

        return pvals

    @property
    def gene_ids(self) -> np.ndarray:
        return np.asarray(self.model_estim.features)

    @property
    def X(self):
        return self.model_estim.X

    @property
    def log_likelihood(self):
        return np.sum(self.model_estim.log_probs(), axis=0)

    @property
    def model_gradient(self):
        return self.model_estim.gradients

    def _ave(self):
        """
        Returns a xr.DataArray containing the mean expression by gene

        :return: xr.DataArray
        """

        return np.mean(self.model_estim.X, axis=0)

    def log_fold_change(self, base=np.e, **kwargs):
        """
        Returns matrix of fold changes per gene
        """
        if self._logfc is None:
            groups = self.groups
            num_features = self.model_estim.X.shape[1]

            logfc = np.tile(np.NaN, [len(groups), len(groups), num_features])
            logfc[np.eye(logfc.shape[0]).astype(bool)] = 0

            theta_mle = self._theta_mle

            for i, g1 in enumerate(groups):
                for j, g2 in enumerate(groups[(i + 1):]):
                    j = j + i + 1

                    logfc[i, j] = theta_mle[j] - theta_mle[i]
                    logfc[j, i] = -logfc[i, j]

            self._logfc = logfc

        if base == np.e:
            return self._logfc
        else:
            return self._logfc / np.log(base)

    def _check_groups(self, group1, group2):
        if group1 not in self.groups:
            raise ValueError('group1 not recognized')
        if group2 not in self.groups:
            raise ValueError('group2 not recognized')

    def pval_pair(self, group1, group2):
        self._check_groups(group1, group2)
        return self.pval[self.groups.index(group1), self.groups.index(group2), :]

    def qval_pair(self, group1, group2):
        self._check_groups(group1, group2)
        return self.qval[self.groups.index(group1), self.groups.index(group2), :]

    def log_fold_change_pair(self, group1, group2, base=np.e):
        self._check_groups(group1, group2)
        return self.log_fold_change(base=base)[self.groups.index(group1), self.groups.index(group2), :]

    def summary(self, qval_thres=None, fc_upper_thres=None,
                fc_lower_thres=None, mean_thres=None,
                **kwargs) -> pd.DataFrame:
        """
        Summarize differential expression results into an output table.
        """
        res = super().summary(**kwargs)

        res = self._threshold_summary(
            res=res,
            qval_thres=qval_thres,
            fc_upper_thres=fc_upper_thres,
            fc_lower_thres=fc_lower_thres,
            mean_thres=mean_thres
        )

        return res

    def summary_pair(self, group1, group2,
                     qval_thres=None, fc_upper_thres=None,
                     fc_lower_thres=None, mean_thres=None,
                     **kwargs) -> pd.DataFrame:
        """
        Summarize differential expression results into an output table.

        :return: pandas.DataFrame with the following columns:

            - gene: the gene id's
            - pval: the per-gene p-value of the selected test
            - qval: the per-gene q-value of the selected test
            - log2fc: the per-gene log2 fold change of the selected test
            - mean: the mean expression of the gene across all groups
        """
        assert self.gene_ids is not None

        res = pd.DataFrame({
            "gene": self.gene_ids,
            "pval": self.pval_pair(group1=group1, group2=group2),
            "qval": self.qval_pair(group1=group1, group2=group2),
            "log2fc": self.log_fold_change_pair(group1=group1, group2=group2, base=2),
            "mean": np.asarray(self.mean)
        })

        res = self._threshold_summary(
            res=res,
            qval_thres=qval_thres,
            fc_upper_thres=fc_upper_thres,
            fc_lower_thres=fc_lower_thres,
            mean_thres=mean_thres
        )

        return res

class DifferentialExpressionTestZTestLazy(_DifferentialExpressionTestMulti):
    """
    Pairwise unit_test between more than 2 groups per gene with lazy evaluation.

    This class performs pairwise tests upon enquiry only and does not store them
    and is therefore suited so very large group sets for which the lfc and
    p-value matrices of the size [genes, groups, groups] are too big to fit into
    memory.
    """

    model_estim: _Estimation
    _theta_mle: np.ndarray
    _theta_sd: np.ndarray

    def __init__(self, model_estim: _Estimation, grouping, groups, correction_type="global"):
        super().__init__(correction_type=correction_type)
        self.model_estim = model_estim
        self.grouping = grouping
        if isinstance(groups, list):
            self.groups = groups
        else:
            self.groups = groups.tolist()

        # values of parameter estimates: coefficients x genes array with one coefficient per group
        self._theta_mle = model_estim.par_link_loc
        # standard deviation of estimates: coefficients x genes array with one coefficient per group
        # theta_sd = sqrt(diagonal(fisher_inv))
        self._theta_sd = np.sqrt(np.diagonal(model_estim.fisher_inv, axis1=-2, axis2=-1)).T

    def _correction(self, pvals, method="fdr_bh") -> np.ndarray:
        """
        Performs multiple testing corrections available in statsmodels.stats.multitest.multipletests().

        This overwrites the parent function which uses self.pval which is not used in this
        lazy implementation.

        :param pvals: P-value array to correct.
        :param method: Multiple testing correction method.
            Browse available methods in the annotation of statsmodels.stats.multitest.multipletests().
        """
        if self._correction_type.lower() == "global":
            pval_shape = pvals.shape
            pvals = np.reshape(pvals, -1)
            qvals = correction.correct(pvals=pvals, method=method)
            qvals = np.reshape(qvals, pval_shape)
        elif self._correction_type.lower() == "by_test":
            qvals = np.apply_along_axis(
                func1d=lambda p: correction.correct(pvals=p, method=method),
                axis=-1,
                arr=pvals,
            )
        else:
            raise ValueError("method " + method + " not recognized in _correction()")

        return qvals

    def _test(self, **kwargs):
        """
        This function is not available in lazy results evaluation as it would
        require all pairwise tests to be performed.
        """
        pass

    def _test_pairs(self, groups0, groups1, **kwargs):
        num_features = self.model_estim.X.shape[1]

        pvals = np.tile(np.NaN, [len(groups0), len(groups1), num_features])

        for i,g0 in enumerate(groups0):
            for j,g1 in enumerate(groups1):
                if g0 != g1:
                    pvals[i, j] = stats.two_coef_z_test(
                        theta_mle0=self._theta_mle[g0],
                        theta_mle1=self._theta_mle[g1],
                        theta_sd0=self._theta_sd[g0],
                        theta_sd1=self._theta_sd[g1]
                    )
                else:
                    pvals[i, j] = 1

        return pvals

    @property
    def gene_ids(self) -> np.ndarray:
        return np.asarray(self.model_estim.features)

    @property
    def X(self):
        return self.model_estim.X

    @property
    def log_likelihood(self):
        return np.sum(self.model_estim.log_probs(), axis=0)

    @property
    def model_gradient(self):
        return self.model_estim.gradients

    def _ave(self):
        """
        Returns a xr.DataArray containing the mean expression by gene

        :return: xr.DataArray
        """

        return np.mean(self.model_estim.X, axis=0)

    @property
    def pval(self, **kwargs):
        """
        This function is not available in lazy results evaluation as it would
        require all pairwise tests to be performed.
        """
        pass

    @property
    def qval(self, **kwargs):
        """
        This function is not available in lazy results evaluation as it would
        require all pairwise tests to be performed.
        """
        pass

    def log_fold_change(self, base=np.e, **kwargs):
        """
        This function is not available in lazy results evaluation as it would
        require all pairwise tests to be performed.
        """
        pass

    def summary(self, qval_thres=None, fc_upper_thres=None,
                fc_lower_thres=None, mean_thres=None,
                **kwargs) -> pd.DataFrame:
        """
        This function is not available in lazy results evaluation as it would
        require all pairwise tests to be performed.
        """
        pass

    def _check_groups(self, groups0, groups1):
        if isinstance(groups0, list)==False:
            groups0 = [groups0]
        if isinstance(groups1, list)==False:
            groups1 = [groups1]
        for g in groups0:
            if g not in self.groups:
                raise ValueError('groups0 element '+str(g)+' not recognized')
        for g in groups1:
            if g not in self.groups:
                raise ValueError('groups1 element '+str(g)+' not recognized')

    def _groups_idx(self, groups):
        if isinstance(groups, list)==False:
            groups = [groups]
        return np.array([self.groups.index(x) for x in groups])

    def pval_pairs(self, groups0=None, groups1=None):
        """
        Return p-values for all pairwise comparisons of groups0 and groups1.

        If you want to test one group (such as a control) against all other groups
        (one test for each other group), give the control group id in groups0
        and leave groups1=None, groups1 is then set to the full set of all groups.

        :param groups0: First set of groups in pair-wise comparison.
        :param groups1: Second set of groups in pair-wise comparison.
        :return: P-values of pair-wise comparison.
        """
        if groups0 is None:
            groups0 = self.groups
        if groups1 is None:
            groups1 = self.groups
        self._check_groups(groups0, groups1)
        groups0 = self._groups_idx(groups0)
        groups1 = self._groups_idx(groups1)
        return self._test_pairs(groups0=groups0, groups1=groups1)

    def qval_pairs(self, groups0=None, groups1=None, method="fdr_bh", **kwargs):
        """
        Return multiple testing-corrected p-values for all
        pairwise comparisons of groups0 and groups1.

        If you want to test one group (such as a control) against all other groups
        (one test for each other group), give the control group id in groups0
        and leave groups1=None, groups1 is then set to the full set of all groups.

        :param groups0: First set of groups in pair-wise comparison.
        :param groups1: Second set of groups in pair-wise comparison.
        :param method: Multiple testing correction method.
            Browse available methods in the annotation of statsmodels.stats.multitest.multipletests().
        :return: Multiple testing-corrected p-values of pair-wise comparison.
        """
        if groups0 is None:
            groups0 = self.groups
        if groups1 is None:
            groups1 = self.groups
        self._check_groups(groups0, groups1)
        groups0 = self._groups_idx(groups0)
        groups1 = self._groups_idx(groups1)
        pval = self.pval_pair(groups0=groups0, groups1=groups1)
        return self._correction(pval=pval, method=method, **kwargs)

    def log_fold_change_pairs(self, groups0=None, groups1=None, base=np.e):
        """
        Return log-fold changes for all pairwise comparisons of groups0 and groups1.

        If you want to test one group (such as a control) against all other groups
        (one test for each other group), give the control group id in groups0
        and leave groups1=None, groups1 is then set to the full set of all groups.

        :param groups0: First set of groups in pair-wise comparison.
        :param groups1: Second set of groups in pair-wise comparison.
        :param base: Base of logarithm of log-fold change.
        :return: P-values of pair-wise comparison.
        """
        if groups0 is None:
            groups0 = self.groups
        if groups1 is None:
            groups1 = self.groups
        self._check_groups(groups0, groups1)
        groups0 = self._groups_idx(groups0)
        groups1 = self._groups_idx(groups1)

        num_features = self._theta_mle.shape[1]

        logfc = np.zeros(shape=(len(groups0), len(groups1), num_features))
        for i,g0 in enumerate(groups0):
            for j,g1 in enumerate(groups1):
                logfc[i,j,:] = self._theta_mle[g0,:].values - self._theta_mle[g1,:].values

        if base == np.e:
            return logfc
        else:
            return logfc / np.log(base)

    def summary_pair(self, group0, group1,
                     qval_thres=None, fc_upper_thres=None,
                     fc_lower_thres=None, mean_thres=None,
                     **kwargs) -> pd.DataFrame:
        """
        Summarize differential expression results of single pairwose comparison
        into an output table.

        :param group0: Firt group in pair-wise comparison.
        :param group1: Second group in pair-wise comparison.
        :return: pandas.DataFrame with the following columns:

            - gene: the gene id's
            - pval: the per-gene p-value of the selected test
            - qval: the per-gene q-value of the selected test
            - log2fc: the per-gene log2 fold change of the selected test
            - mean: the mean expression of the gene across all groups
        """
        assert self.gene_ids is not None

        if len(group0) != 1:
            raise ValueError("group0 should only contain one entry in summary_pair()")
        if len(group1) != 1:
            raise ValueError("group1 should only contain one entry in summary_pair()")

        pval = self.pval_pairs(groups0=group0, groups1=group1)
        qval = self._correction(pvals=pval, **kwargs)
        res = pd.DataFrame({
            "gene": self.gene_ids,
            "pval": pval.flatten(),
            "qval": qval.flatten(),
            "log2fc": self.log_fold_change_pairs(groups0=group0, groups1=group1, base=2).flatten(),
            "mean": np.asarray(self.mean)
        })

        res = self._threshold_summary(
            res=res,
            qval_thres=qval_thres,
            fc_upper_thres=fc_upper_thres,
            fc_lower_thres=fc_lower_thres,
            mean_thres=mean_thres
        )

        return res

    def summary_pairs(self, groups0, groups1=None,
                     qval_thres=None, fc_upper_thres=None,
                     fc_lower_thres=None, mean_thres=None,
                     **kwargs) -> pd.DataFrame:
        """
        Summarize differential expression results of a set of
        pairwise comparisons into an output table.

        :param groups0: First set of groups in pair-wise comparison.
        :param groups1: Second set of groups in pair-wise comparison.
        :return: pandas.DataFrame with the following columns:

            - gene: the gene id's
            - pval: the minimum per-gene p-value of all tests
            - qval: the minimum per-gene q-value of all tests
            - log2fc: the maximal/minimal (depending on which one is higher) log2 fold change of the genes
            - mean: the mean expression of the gene across all groups
        """
        assert self.gene_ids is not None
        if groups1 is None:
            groups1 = self.groups

        pval = self.pval_pairs(groups0=groups0, groups1=groups1)
        qval = self._correction(pvals=pval, **kwargs)

        # calculate maximum logFC of lower triangular fold change matrix
        raw_logfc = self.log_fold_change_pairs(groups0=groups0, groups1=groups1, base=2)

        # first flatten all dimensions up to the last 'gene' dimension
        flat_logfc = raw_logfc.reshape(-1, raw_logfc.shape[-1])
        # next, get argmax of flattened logfc and unravel the true indices from it
        r, c = np.unravel_index(flat_logfc.argmax(0), raw_logfc.shape[:2])
        # if logfc is maximal in the lower triangular matrix, multiply it with -1
        logfc = raw_logfc[r, c, np.arange(raw_logfc.shape[-1])] * np.where(r <= c, 1, -1)

        res = pd.DataFrame({
            "gene": self.gene_ids,
            "pval": np.min(pval, axis=(0,1)),
            "qval": np.min(qval, axis=(0,1)),
            "log2fc": np.asarray(logfc),
            "mean": np.asarray(self.mean)
        })

        res = self._threshold_summary(
            res=res,
            qval_thres=qval_thres,
            fc_upper_thres=fc_upper_thres,
            fc_lower_thres=fc_lower_thres,
            mean_thres=mean_thres
        )

        return res


class DifferentialExpressionTestVsRest(_DifferentialExpressionTestMulti):
    """
    Tests between between each group and the rest for more than 2 groups per gene.
    """

    def __init__(self, gene_ids, pval, logfc, ave, groups, tests, correction_type: str):
        super().__init__(correction_type=correction_type)
        self._gene_ids = np.asarray(gene_ids)
        self._pval = pval
        self._logfc = logfc
        self._mean = ave
        self.groups = list(np.asarray(groups))
        self._tests = tests

        q = self.qval

    @property
    def tests(self):
        """
        If `keep_full_test_objs` was set to `True`, this will return a matrix of differential expression tests.
        """
        if self._tests is None:
            raise ValueError("Individual tests were not kept!")

        return self._tests

    @property
    def gene_ids(self) -> np.ndarray:
        return self._gene_ids

    @property
    def X(self) -> np.ndarray:
        return None

    def log_fold_change(self, base=np.e, **kwargs):
        if base == np.e:
            return self._logfc
        else:
            return self._logfc / np.log(base)

    def _check_group(self, group):
        if group not in self.groups:
            raise ValueError('group "%s" not recognized' % group)

    def pval_group(self, group):
        self._check_group(group)
        return self.pval[0, self.groups.index(group), :]

    def qval_group(self, group):
        self._check_group(group)
        return self.qval[0, self.groups.index(group), :]

    def log_fold_change_group(self, group, base=np.e):
        self._check_group(group)
        return self.log_fold_change(base=base)[0, self.groups.index(group), :]

    def summary(self, qval_thres=None, fc_upper_thres=None,
                fc_lower_thres=None, mean_thres=None,
                **kwargs) -> pd.DataFrame:
        """
        Summarize differential expression results into an output table.
        """
        res = super().summary(**kwargs)

        res = self._threshold_summary(
            res=res,
            qval_thres=qval_thres,
            fc_upper_thres=fc_upper_thres,
            fc_lower_thres=fc_lower_thres,
            mean_thres=mean_thres
        )

        return res

    def summary_group(self, group,
                      qval_thres=None, fc_upper_thres=None,
                      fc_lower_thres=None, mean_thres=None,
                      **kwargs) -> pd.DataFrame:
        """
        Summarize differential expression results into an output table.

        :return: pandas.DataFrame with the following columns:

            - gene: the gene id's
            - pval: the per-gene p-value of the selected test
            - qval: the per-gene q-value of the selected test
            - log2fc: the per-gene log2 fold change of the selected test
            - mean: the mean expression of the gene across all groups
        """
        assert self.gene_ids is not None

        res = pd.DataFrame({
            "gene": self.gene_ids,
            "pval": self.pval_group(group=group),
            "qval": self.qval_group(group=group),
            "log2fc": self.log_fold_change_group(group=group, base=2),
            "mean": np.asarray(self.mean)
        })

        res = self._threshold_summary(
            res=res,
            qval_thres=qval_thres,
            fc_upper_thres=fc_upper_thres,
            fc_lower_thres=fc_lower_thres,
            mean_thres=mean_thres
        )

        return res


class DifferentialExpressionTestByPartition(_DifferentialExpressionTestMulti):
    """
    Stores a particular test performed within each partition of the data set.
    """

    def __init__(self, partitions, tests, ave, correction_type: str = "by_test"):
        super().__init__(correction_type=correction_type)
        self.partitions = list(np.asarray(partitions))
        self._tests = tests
        self._gene_ids = tests[0].gene_ids
        self._pval = np.expand_dims(np.vstack([x.pval for x in tests]), axis=0)
        self._logfc = np.expand_dims(np.vstack([x.log_fold_change() for x in tests]), axis=0)
        self._mean = ave

        q = self.qval

    @property
    def gene_ids(self) -> np.ndarray:
        return self._gene_ids

    @property
    def X(self) -> np.ndarray:
        return None

    def log_fold_change(self, base=np.e, **kwargs):
        if base == np.e:
            return self._logfc
        else:
            return self._logfc / np.log(base)

    def _check_partition(self, partition):
        if partition not in self.partitions:
            raise ValueError('partition "%s" not recognized' % partition)

    @property
    def tests(self, partition=None):
        """
        If `keep_full_test_objs` was set to `True`, this will return a matrix of differential expression tests.

        :param partition: The partition for which to return the test. Returns full list if None.
        """
        if self._tests is None:
            raise ValueError("Individual tests were not kept!")

        if partition is None:
            return self._tests
        else:
            self._check_partition(partition)
            return self._tests[self.partitions.index(partition)]

    def summary(self, qval_thres=None, fc_upper_thres=None,
                fc_lower_thres=None, mean_thres=None,
                **kwargs) -> pd.DataFrame:
        """
        Summarize differential expression results into an output table.
        """
        res = super().summary(**kwargs)

        res = self._threshold_summary(
            res=res,
            qval_thres=qval_thres,
            fc_upper_thres=fc_upper_thres,
            fc_lower_thres=fc_lower_thres,
            mean_thres=mean_thres
        )

        return res


class _DifferentialExpressionTestCont(_DifferentialExpressionTestSingle):
    _de_test: _DifferentialExpressionTestSingle
    _model_estim: _Estimation
    _size_factors: np.ndarray
    _continuous_coords: np.ndarray
    _spline_coefs: list

    def __init__(
            self,
            de_test: _DifferentialExpressionTestSingle,
            model_estim: _Estimation,
            size_factors: np.ndarray,
            continuous_coords: str,
            spline_coefs: list
    ):
        self._de_test = de_test
        self._model_estim = model_estim
        self._size_factors = size_factors
        self._continuous_coords = continuous_coords
        self._spline_coefs = spline_coefs

    @property
    def gene_ids(self) -> np.ndarray:
        return self._de_test.gene_ids

    @property
    def X(self):
        return self._de_test.X

    @property
    def pval(self) -> np.ndarray:
        return self._de_test.pval

    @property
    def qval(self) -> np.ndarray:
        return self._de_test.qval

    @property
    def mean(self) -> np.ndarray:
        return self._de_test.mean

    @property
    def log_likelihood(self) -> np.ndarray:
        return self._de_test.log_likelihood

    def summary(self, nonnumeric=False, qval_thres=None, fc_upper_thres=None,
                fc_lower_thres=None, mean_thres=None) -> pd.DataFrame:
        """
        Summarize differential expression results into an output table.

        :param nonnumeric: Whether to include non-numeric covariates in fit.
        """
        # Collect summary from differential test object.
        res = self._de_test.summary()
        # Overwrite fold change with fold change from temporal model.
        # Note that log2_fold_change calls log_fold_change from this class
        # and not from the self._de_test object,
        # which is called by self._de_test.summary().
        res['log2fc'] = self.log2_fold_change()

        res = self._threshold_summary(
            res=res,
            qval_thres=qval_thres,
            fc_upper_thres=fc_upper_thres,
            fc_lower_thres=fc_lower_thres,
            mean_thres=mean_thres
        )

        return res

    def log_fold_change(self, base=np.e, genes=None, nonnumeric=False):
        """
        Return log_fold_change based on fitted expression values by gene.

        The log_fold_change is defined as the log of the fold change
        from the minimal to the maximal fitted value by gene.

        :param base: Basis of logarithm.
        :param genes: Genes for which to return maximum fitted value. Defaults
            to all genes if None.
        :param nonnumeric: Whether to include non-numeric covariates in fit.
        :return: Log-fold change of fitted expression value by gene.
        """
        if genes is None:
            genes = np.asarray(range(self.X.shape[1]))
        else:
            genes = self._idx_genes(genes)

        fc = self.max(genes=genes, nonnumeric=nonnumeric) - \
             self.min(genes=genes, nonnumeric=nonnumeric)
        fc = np.nextafter(0, 1, out=fc, where=fc == 0)

        return np.log(fc) / np.log(base)

    def _filter_genes_str(self, genes: list):
        """
        Filter genes indexed by ID strings by list of genes given in data set.

        :param genes: List of genes to filter.
        :return: Filtered list of genes
        """
        genes_found = np.array([x in self.gene_ids for x in genes])
        if any(genes_found == False):
            logger.info("did not find some genes, omitting")
            genes = genes[genes_found]
        return genes

    def _filter_genes_int(self, genes: list):
        """
        Filter genes indexed by integers by gene list length.

        :param genes: List of genes to filter.
        :return: Filtered list of genes
        """
        genes_found = np.array([x < self.X.shape[1] for x in genes])
        if any(genes_found == False):
            logger.info("did not find some genes, omitting")
            genes = genes[genes_found]
        return genes

    def _idx_genes(self, genes):
        if not isinstance(genes, list):
            if isinstance(genes, np.ndarray):
                genes = genes.tolist()
            else:
                genes = [genes]

        if isinstance(genes[0], str):
            genes = self._filter_genes_str(genes)
            genes = np.array([self.gene_ids.index(x) for x in genes])
        elif isinstance(genes[0], int) or isinstance(genes[0], np.int64):
            genes = self._filter_genes_int(genes)
        else:
            raise ValueError("only string and integer elements allowed in genes")
        return genes

    def _spline_par_loc_idx(self, intercept=True):
        """
        Get indices of spline basis model parameters in
        entire location parameter model parameter set.

        :param intercept: Whether to include intercept.
        :return: Indices of spline basis parameters of location model.
        """
        par_loc_names = self._model_estim.design_loc.coords['design_loc_params'].values.tolist()
        idx = [par_loc_names.index(x) for x in self._spline_coefs]
        if 'Intercept' in par_loc_names and intercept == True:
            idx = np.concatenate([np.where([[x == 'Intercept' for x in par_loc_names]])[0], idx])
        return idx

    def _continuous_model(self, idx, nonnumeric=False):
        """
        Recover continuous fit for a gene.

        :param idx: Index of genes to recover fit for.
        :param nonnumeric: Whether to include non-numeric covariates in fit.
        :return: Continuuos fit for each cell for given gene.
        """
        idx = np.asarray(idx)
        if nonnumeric:
            mu = np.matmul(self._model_estim.design_loc.values,
                           self._model_estim.par_link_loc[:,idx])
            if self._size_factors is not None:
                mu = mu + self._size_factors
        else:
            idx_basis = self._spline_par_loc_idx(intercept=True)
            mu = np.matmul(self._model_estim.design_loc[:,idx_basis].values,
                           self._model_estim.par_link_loc[idx_basis, idx])

        mu = np.exp(mu)
        return mu

    def max(self, genes, nonnumeric=False):
        """
        Return maximum fitted expression value by gene.

        :param genes: Genes for which to return maximum fitted value.
        :param nonnumeric: Whether to include non-numeric covariates in fit.
        :return: Maximum fitted expression value by gene.
        """
        genes = self._idx_genes(genes)
        return np.array([np.max(self._continuous_model(idx=i, nonnumeric=nonnumeric))
                         for i in genes])

    def min(self, genes, nonnumeric=False):
        """
        Return minimum fitted expression value by gene.

        :param genes: Genes for which to return maximum fitted value.
        :param nonnumeric: Whether to include non-numeric covariates in fit.
        :return: Maximum fitted expression value by gene.
        """
        genes = self._idx_genes(genes)
        return np.array([np.min(self._continuous_model(idx=i, nonnumeric=nonnumeric))
                         for i in genes])

    def argmax(self, genes, nonnumeric=False):
        """
        Return maximum fitted expression value by gene.

        :param genes: Genes for which to return maximum fitted value.
        :param nonnumeric: Whether to include non-numeric covariates in fit.
        :return: Maximum fitted expression value by gene.
        """
        genes = self._idx_genes(genes)
        idx = np.array([np.argmax(self._continuous_model(idx=i, nonnumeric=nonnumeric))
                        for i in genes])
        return self._continuous_coords[idx]

    def argmin(self, genes, nonnumeric=False):
        """
        Return minimum fitted expression value by gene.

        :param genes: Genes for which to return maximum fitted value.
        :param nonnumeric: Whether to include non-numeric covariates in fit.
        :return: Maximum fitted expression value by gene.
        """
        genes = self._idx_genes(genes)
        idx = np.array([np.argmin(self._continuous_model(idx=i, nonnumeric=nonnumeric))
                        for i in genes])
        return self._continuous_coords[idx]

    def plot_genes(
            self,
            genes,
            hue=None,
            size=1,
            log=True,
            nonnumeric=False,
            save=None,
            show=True,
            ncols=2,
            row_gap=0.3,
            col_gap=0.25
    ):
        """
        Plot observed data and spline fits of selected genes.

        :param genes: Gene IDs to plot.
        :param hue: Confounder to include in plot.
        :param size: Point size.
        :param nonnumeric:
        :param save: Path+file name stem to save plots to.
            File will be save+"_genes.png". Does not save if save is None.
        :param show: Whether to display plot.
        :param ncols: Number of columns in plot grid if multiple genes are plotted.
        :param row_gap: Vertical gap between panel rows relative to panel height.
        :param col_gap: Horizontal gap between panel columns relative to panel width.
        :return: Matplotlib axis objects.
        """

        import seaborn as sns
        import matplotlib.pyplot as plt
        from matplotlib import gridspec
        from matplotlib import rcParams

        plt.ioff()

        gene_idx = self._idx_genes(genes)

        # Set up gridspec.
        ncols = ncols if len(gene_idx) > ncols else len(gene_idx)
        nrows = len(gene_idx) // ncols + (len(gene_idx) - (len(gene_idx) // ncols) * ncols)
        gs = gridspec.GridSpec(
            nrows=nrows,
            ncols=ncols,
            hspace=row_gap,
            wspace=col_gap
        )

        # Define figure size based on panel number and grid.
        fig = plt.figure(
            figsize=(
                ncols * rcParams['figure.figsize'][0],  # width in inches
                nrows * rcParams['figure.figsize'][1] * (1 + row_gap)  # height in inches
            )
        )

        # Build axis objects in loop.
        axs = []
        for i, g in enumerate(gene_idx):
            ax = plt.subplot(gs[i])
            axs.append(ax)

            y = self.X[:, genes[0]]
            yhat = self._continuous_model(idx=g, nonnumeric=nonnumeric)
            if log:
                y = np.log(y + 1)
                yhat = np.log(yhat + 1)

            sns.scatterplot(
                x=self._continuous_coords,
                y=y,
                hue=hue,
                size=size,
                ax=ax,
                legend=False
            )
            sns.lineplot(
                x=self._continuous_coords,
                y=yhat,
                hue=hue,
                ax=ax
            )

            ax.set_title(genes[i])
            ax.set_xlabel("continuous")
            if log:
                ax.set_ylabel("log expression")
            else:
                ax.set_ylabel("expression")

        # Save, show and return figure.
        if save is not None:
            plt.savefig(save+'_genes.png')

        if show:
            plt.show()

        plt.close(fig)

        return axs


    def plot_heatmap(
            self,
            genes,
            save=None,
            show=True,
            transform: str = "zscore",
            nticks=10,
            cmap: str = "YlGnBu",
            width=10,
            height_per_gene=0.5
    ):
        """
        Plot observed data and spline fits of selected genes.

        :param genes: Gene IDs to plot.
        :param save: Path+file name stem to save plots to.
            File will be save+"_genes.png". Does not save if save is None.
        :param show: Whether to display plot.
        :param transform: Gene-wise transform to use.
        :param nticks: Number of x ticks.
        :param cmap: matplotlib cmap.
        :param width: Width of heatmap figure.
        :param height_per_gene: Height of each row (gene) in heatmap figure.
        :return: Matplotlib axis objects.
        """
        import seaborn as sns
        import matplotlib.pyplot as plt

        plt.ioff()

        gene_idx = self._idx_genes(genes)

        # Define figure.
        fig = plt.figure(figsize=(width, height_per_gene * len(gene_idx)))
        ax = fig.add_subplot(111)

        # Build heatmap matrix.
        ## Add in data.
        data = np.array([
            self._continuous_model(idx=g, nonnumeric=False)
            for i, g in enumerate(gene_idx)
        ])
        ## Order columns by continuous covariate.
        idx_x_sorted = np.argsort(self._continuous_coords)
        data = data[:, idx_x_sorted]
        xcoord = self._continuous_coords[idx_x_sorted]

        if transform.lower() == "log10":
            data = np.nextafter(0, 1, out=data, where=data == 0)
            data = np.log(data) / np.log(10)
        elif transform.lower() == "zscore":
            mu = np.mean(data, axis=0)
            sd = np.std(data, axis=0)
            sd = np.nextafter(0, 1, out=sd, where=sd == 0)
            data = np.array([(x - mu[i]) / sd[i] for i, x in enumerate(data)])
        elif transform.lower() == "none":
            pass
        else:
            raise ValueError("transform not recognized in plot_heatmap()")

        # Create heatmap.
        sns.heatmap(data=data, cmap=cmap, ax=ax)

        # Set up axis labels.
        xtick_pos = np.asarray(np.round(np.linspace(
            start=0,
            stop=data.shape[1] - 1,
            num=nticks,
            endpoint=True
        )), dtype=int)
        xtick_lab = [str(np.round(xcoord[np.argmin(np.abs(xcoord - xcoord[i]))], 2))
                     for i in xtick_pos]
        ax.set_xticks(xtick_pos)
        ax.set_xticklabels(xtick_lab)
        ax.set_xlabel("continuous")
        plt.yticks(np.arange(len(genes)), genes, rotation='horizontal')
        ax.set_ylabel("genes")

        # Save, show and return figure.
        if save is not None:
            plt.savefig(save + '_genes.png')

        if show:
            plt.show()

        plt.close(fig)

        return ax


class DifferentialExpressionTestWaldCont(_DifferentialExpressionTestCont):
    de_test: DifferentialExpressionTestWald

    def __init__(
            self,
            de_test: DifferentialExpressionTestWald,
            size_factors: np.ndarray,
            continuous_coords: np.ndarray,
            spline_coefs: list
    ):
        super().__init__(
            de_test=de_test,
            model_estim=de_test.model_estim,
            size_factors=size_factors,
            continuous_coords=continuous_coords,
            spline_coefs=spline_coefs
        )


class DifferentialExpressionTestLRTCont(_DifferentialExpressionTestCont):
    de_test: DifferentialExpressionTestLRT

    def __init__(self,
            de_test: DifferentialExpressionTestLRT,
            size_factors: np.ndarray,
            continuous_coords: np.ndarray,
            spline_coefs: list
    ):
        super().__init__(
            de_test=de_test,
            model_estim=de_test.full_estim,
            size_factors=size_factors,
            continuous_coords=continuous_coords,
            spline_coefs=spline_coefs
        )


def _parse_gene_names(data, gene_names):
    if gene_names is None:
        if anndata is not None and (isinstance(data, anndata.AnnData) or isinstance(data, anndata.base.Raw)):
            gene_names = data.var_names
        elif isinstance(data, xr.DataArray):
            gene_names = data["features"]
        elif isinstance(data, xr.Dataset):
            gene_names = data["features"]
        else:
            raise ValueError("Missing gene names")

    return np.asarray(gene_names)


def _parse_data(data, gene_names) -> xr.DataArray:
    X = data_utils.xarray_from_data(data, dims=("observations", "features"))
    if gene_names is not None:
        X.coords["features"] = gene_names

    return X


def _parse_sample_description(data, sample_description=None) -> pd.DataFrame:
    if sample_description is None:
        if anndata is not None and isinstance(data, anndata.AnnData):
            sample_description = data_utils.sample_description_from_anndata(
                dataset=data,
            )
        elif isinstance(data, xr.Dataset):
            sample_description = data_utils.sample_description_from_xarray(
                dataset=data,
                dim="observations",
            )
        else:
            raise ValueError(
                "Please specify `sample_description` or provide `data` as xarray.Dataset or anndata.AnnData " +
                "with corresponding sample annotations"
            )

    if anndata is not None and isinstance(data, anndata.base.Raw):
        # anndata.base.Raw does not have attribute shape.
        assert data.X.shape[0] == sample_description.shape[0], \
            "data matrix and sample description must contain same number of cells"
    else:
        assert data.shape[0] == sample_description.shape[0], \
            "data matrix and sample description must contain same number of cells"
    return sample_description


def _parse_size_factors(size_factors, data):
    if size_factors is not None:
        if isinstance(size_factors, pd.core.series.Series):
            size_factors = size_factors.values
        assert size_factors.shape[0] == data.shape[0], "data matrix and size factors must contain same number of cells"
    return size_factors


def design_matrix(
        data=None,
        sample_description: pd.DataFrame = None,
        formula: str = None,
        dmat: pd.DataFrame = None
) -> Union[patsy.design_info.DesignMatrix, xr.Dataset]:
    """ Build design matrix for fit of generalized linear model.

    This is necessary for wald tests and likelihood ratio tests.
    This function only carries through formatting if dmat is directly supplied.

    :param data: input data
    :param formula: model formula.
    :param sample_description: optional pandas.DataFrame containing sample annotations
    :param dmat: model design matrix
    """
    if data is None and sample_description is None and dmat is None:
        raise ValueError("Supply either data or sample_description or dmat.")
    if dmat is None and formula is None:
        raise ValueError("Supply either dmat or formula.")

    if dmat is None:
        sample_description = _parse_sample_description(data, sample_description)
        dmat = data_utils.design_matrix(sample_description=sample_description, formula=formula)

        return dmat
    else:
        ar = xr.DataArray(dmat, dims=("observations", "design_params"))
        ar.coords["design_params"] = dmat.columns

        ds = xr.Dataset({
            "design": ar,
        })

        return ds


def coef_names(
        data=None,
        sample_description: pd.DataFrame = None,
        formula: str = None,
        dmat: pd.DataFrame = None
) -> list:
    """ Output coefficient names of model only.

    :param data: input data
    :param formula: model formula.
    :param sample_description: optional pandas.DataFrame containing sample annotations
    :param dmat: model design matrix
    """
    return design_matrix(
        data=data,
        sample_description=sample_description,
        formula=formula,
        dmat=dmat
    ).design_info.column_names


def _fit(
        noise_model,
        data,
        design_loc,
        design_scale,
        constraints_loc: np.ndarray = None,
        constraints_scale: np.ndarray = None,
        init_model=None,
        init_a: Union[np.ndarray, str] = "AUTO",
        init_b: Union[np.ndarray, str] = "AUTO",
        gene_names=None,
        size_factors=None,
        batch_size: int = None,
        training_strategy: Union[str, List[Dict[str, object]], Callable] = "AUTO",
        quick_scale: bool = None,
        close_session=True,
        dtype="float32"
):
    """
    :param noise_model: str, noise model to use in model-based unit_test. Possible options:

        - 'nb': default
    :param design_loc: Design matrix of location model.
    :param design_loc: Design matrix of scale model.
    :param constraints_loc: : Constraints for location model.
        Array with constraints in rows and model parameters in columns.
        Each constraint contains non-zero entries for the a of parameters that 
        has to sum to zero. This constraint is enforced by binding one parameter
        to the negative sum of the other parameters, effectively representing that
        parameter as a function of the other parameters. This dependent
        parameter is indicated by a -1 in this array, the independent parameters
        of that constraint (which may be dependent at an earlier constraint)
        are indicated by a 1.
    :param constraints_scale: : Constraints for scale model.
        Array with constraints in rows and model parameters in columns.
        Each constraint contains non-zero entries for the a of parameters that 
        has to sum to zero. This constraint is enforced by binding one parameter
        to the negative sum of the other parameters, effectively representing that
        parameter as a function of the other parameters. This dependent
        parameter is indicated by a -1 in this array, the independent parameters
        of that constraint (which may be dependent at an earlier constraint)
        are indicated by a 1.
    :param init_model: (optional) If provided, this model will be used to initialize this Estimator.
    :param init_a: (Optional) Low-level initial values for a.
        Can be:

        - str:
            * "auto": automatically choose best initialization
            * "random": initialize with random values
            * "standard": initialize intercept with observed mean
            * "init_model": initialize with another model (see `ìnit_model` parameter)
            * "closed_form": try to initialize with closed form
        - np.ndarray: direct initialization of 'a'
    :param init_b: (Optional) Low-level initial values for b
        Can be:

        - str:
            * "auto": automatically choose best initialization
            * "random": initialize with random values
            * "standard": initialize with zeros
            * "init_model": initialize with another model (see `ìnit_model` parameter)
            * "closed_form": try to initialize with closed form
        - np.ndarray: direct initialization of 'b'
    :param as_numeric:
        Which columns of sample_description were treated as numeric and
        not as categorical. This yields columns in the design matrix
        which do not correspond to one-hot encoded discrete factors.
        This makes sense for number of genes, time, pseudotime or space
        for example. This is passed to Estimator so that this information
        can be used for initialization.
    :param size_factors: 1D array of transformed library size factors for each cell in the
        same order as in data
    :param batch_size: the batch size to use for the estimator
    :param training_strategy: {str, function, list} training strategy to use. Can be:

        - str: will use Estimator.TrainingStrategy[training_strategy] to train
        - function: Can be used to implement custom training function will be called as
          `training_strategy(estimator)`.
        - list of keyword dicts containing method arguments: Will call Estimator.train() once with each dict of
          method arguments.

          Example:

          .. code-block:: python

              [
                {"learning_rate": 0.5, },
                {"learning_rate": 0.05, },
              ]

          This will run training first with learning rate = 0.5 and then with learning rate = 0.05.
    :param quick_scale: Depending on the optimizer, `scale` will be fitted faster and maybe less accurate.

        Useful in scenarios where fitting the exact `scale` is not absolutely necessary.
    :param dtype: Allows specifying the precision which should be used to fit data.

        Should be "float32" for single precision or "float64" for double precision.
    :param close_session: If True, will finalize the estimator. Otherwise, return the estimator itself.
    """
    provide_optimizers = {
        "gd": pkg_constants.BATCHGLM_OPTIM_GD,
        "adam": pkg_constants.BATCHGLM_OPTIM_ADAM,
        "adagrad": pkg_constants.BATCHGLM_OPTIM_ADAGRAD,
        "rmsprop": pkg_constants.BATCHGLM_OPTIM_RMSPROP,
        "nr": pkg_constants.BATCHGLM_OPTIM_NEWTON,
        "nr_tr": pkg_constants.BATCHGLM_OPTIM_NEWTON_TR,
        "irls": pkg_constants.BATCHGLM_OPTIM_IRLS,
        "irls_tr": pkg_constants.BATCHGLM_OPTIM_IRLS_TR
    }

    if isinstance(training_strategy, str) and training_strategy.lower() == 'bfgs':
        lib_size = np.zeros(data.shape[0])
        if noise_model == "nb" or noise_model == "negative_binomial":
            estim = Estim_BFGS(X=data, design_loc=design_loc, design_scale=design_scale,
                               lib_size=lib_size, batch_size=batch_size, feature_names=gene_names)
            estim.run(nproc=3, maxiter=10000, debug=False)
            model = estim.return_batchglm_formated_model()
        else:
            raise ValueError('base.test(): `noise_model="%s"` not recognized.' % noise_model)
    else:
        if noise_model == "nb" or noise_model == "negative_binomial":
            from batchglm.api.models.glm_nb import Estimator, InputData
        else:
            raise ValueError('base.test(): `noise_model="%s"` not recognized.' % noise_model)

        logger.info("Fitting model...")
        logger.debug(" * Assembling input data...")
        input_data = InputData.new(
            data=data,
            design_loc=design_loc,
            design_scale=design_scale,
            constraints_loc=constraints_loc,
            constraints_scale=constraints_scale,
            size_factors=size_factors,
            feature_names=gene_names,
        )

        logger.debug(" * Set up Estimator...")
        constructor_args = {}
        if batch_size is not None:
            constructor_args["batch_size"] = batch_size
        if quick_scale is not None:
            constructor_args["quick_scale"] = quick_scale
        estim = Estimator(
            input_data=input_data,
            init_model=init_model,
            init_a=init_a,
            init_b=init_b,
            provide_optimizers=provide_optimizers,
            provide_batched=pkg_constants.BATCHGLM_PROVIDE_BATCHED,
            termination_type=pkg_constants.BATCHGLM_TERMINATION_TYPE,
            dtype=dtype,
            **constructor_args
        )

        logger.debug(" * Initializing Estimator...")
        estim.initialize()

        logger.debug(" * Run estimation...")
        # training:
        if callable(training_strategy):
            # call training_strategy if it is a function
            training_strategy(estim)
        else:
            estim.train_sequence(training_strategy=training_strategy)

        if close_session:
            logger.debug(" * Finalize estimation...")
            model = estim.finalize()
        else:
            model = estim
        logger.debug(" * Model fitting done.")

    return model


def lrt(
        data: Union[anndata.AnnData, anndata.base.Raw, xr.DataArray, xr.Dataset, np.ndarray, scipy.sparse.csr_matrix],
        reduced_formula_loc: str,
        full_formula_loc: str,
        reduced_formula_scale: str = "~1",
        full_formula_scale: str = "~1",
        as_numeric: Union[List[str], Tuple[str], str] = (),
        init_a: Union[np.ndarray, str] = "AUTO",
        init_b: Union[np.ndarray, str] = "AUTO",
        gene_names=None,
        sample_description: pd.DataFrame = None,
        noise_model="nb",
        size_factors: np.ndarray = None,
        batch_size: int = None,
        training_strategy: Union[str, List[Dict[str, object]], Callable] = "DEFAULT",
        quick_scale: bool = False,
        dtype="float64",
        **kwargs
):
    """
    Perform log-likelihood ratio test for differential expression for each gene.

    Note that lrt() does not support constraints in its current form. Please
    use wald() for constraints.

    :param data: Array-like, xr.DataArray, xr.Dataset or anndata.Anndata object containing observations.
        Input data
    :param reduced_formula_loc: formula
        Reduced model formula for location and scale parameter models.
        If not specified, `reduced_formula` will be used instead.
    :param full_formula_loc: formula
        Full model formula for location parameter model.
        If not specified, `full_formula` will be used instead.
    :param reduced_formula_scale: formula
        Reduced model formula for scale parameter model.
        If not specified, `reduced_formula` will be used instead.
    :param full_formula_scale: formula
        Full model formula for scale parameter model.
        If not specified, `reduced_formula_scale` will be used instead.
    :param as_numeric:
        Which columns of sample_description to treat as numeric and
        not as categorical. This yields columns in the design matrix
        which do not correpond to one-hot encoded discrete factors.
        This makes sense for number of genes, time, pseudotime or space
        for example.
    :param init_a: (Optional) Low-level initial values for a.
        Can be:

        - str:
            * "auto": automatically choose best initialization
            * "random": initialize with random values
            * "standard": initialize intercept with observed mean
            * "init_model": initialize with another model (see `ìnit_model` parameter)
            * "closed_form": try to initialize with closed form
        - np.ndarray: direct initialization of 'a'
    :param init_b: (Optional) Low-level initial values for b
        Can be:

        - str:
            * "auto": automatically choose best initialization
            * "random": initialize with random values
            * "standard": initialize with zeros
            * "init_model": initialize with another model (see `ìnit_model` parameter)
            * "closed_form": try to initialize with closed form
        - np.ndarray: direct initialization of 'b'
    :param gene_names: optional list/array of gene names which will be used if `data` does not implicitly store these
    :param sample_description: optional pandas.DataFrame containing sample annotations
    :param noise_model: str, noise model to use in model-based unit_test. Possible options:

        - 'nb': default
    :param size_factors: 1D array of transformed library size factors for each cell in the 
        same order as in data
    :param batch_size: the batch size to use for the estimator
    :param training_strategy: {str, function, list} training strategy to use. Can be:

        - str: will use Estimator.TrainingStrategy[training_strategy] to train
        - function: Can be used to implement custom training function will be called as
          `training_strategy(estimator)`.
        - list of keyword dicts containing method arguments: Will call Estimator.train() once with each dict of
          method arguments.

          Example:

          .. code-block:: python

              [
                {"learning_rate": 0.5, },
                {"learning_rate": 0.05, },
              ]

          This will run training first with learning rate = 0.5 and then with learning rate = 0.05.
    :param quick_scale: Depending on the optimizer, `scale` will be fitted faster and maybe less accurate.

        Useful in scenarios where fitting the exact `scale` is not absolutely necessary.
    :param dtype: Allows specifying the precision which should be used to fit data.

        Should be "float32" for single precision or "float64" for double precision.
    :param kwargs: [Debugging] Additional arguments will be passed to the _fit method.
    """
    # TODO test nestedness
    if len(kwargs) != 0:
        logger.info("additional kwargs: %s", str(kwargs))

    if isinstance(as_numeric, str):
        as_numeric = [as_numeric]

    gene_names = _parse_gene_names(data, gene_names)
    X = _parse_data(data, gene_names)
    sample_description = _parse_sample_description(data, sample_description)
    size_factors = _parse_size_factors(size_factors=size_factors, data=X)

    full_design_loc = data_utils.design_matrix(
        sample_description=sample_description,
        formula=full_formula_loc,
        as_categorical=[False if x in as_numeric else True for x in sample_description.columns.values]
    )
    reduced_design_loc = data_utils.design_matrix(
        sample_description=sample_description,
        formula=reduced_formula_loc,
        as_categorical=[False if x in as_numeric else True for x in sample_description.columns.values]
    )
    full_design_scale = data_utils.design_matrix(
        sample_description=sample_description,
        formula=full_formula_scale,
        as_categorical=[False if x in as_numeric else True for x in sample_description.columns.values]
    )
    reduced_design_scale = data_utils.design_matrix(
        sample_description=sample_description,
        formula=reduced_formula_scale,
        as_categorical=[False if x in as_numeric else True for x in sample_description.columns.values]
    )

    reduced_model = _fit(
        noise_model=noise_model,
        data=X,
        design_loc=reduced_design_loc,
        design_scale=reduced_design_scale,
        constraints_loc=None,
        constraints_scale=None,
        init_a=init_a,
        init_b=init_b,
        gene_names=gene_names,
        size_factors=size_factors,
        batch_size=batch_size,
        training_strategy=training_strategy,
        quick_scale=quick_scale,
        dtype=dtype,
        **kwargs
    )
    full_model = _fit(
        noise_model=noise_model,
        data=X,
        design_loc=full_design_loc,
        design_scale=full_design_scale,
        constraints_loc=None,
        constraints_scale=None,
        gene_names=gene_names,
        init_a="init_model",
        init_b="init_model",
        init_model=reduced_model,
        size_factors=size_factors,
        batch_size=batch_size,
        training_strategy=training_strategy,
        quick_scale=quick_scale,
        dtype=dtype,
        **kwargs
    )

    de_test = DifferentialExpressionTestLRT(
        sample_description=sample_description,
        full_design_loc_info=full_design_loc.design_info,
        full_estim=full_model,
        reduced_design_loc_info=reduced_design_loc.design_info,
        reduced_estim=reduced_model,
    )

    return de_test


def wald(
        data: Union[anndata.AnnData, anndata.base.Raw, xr.DataArray, xr.Dataset, np.ndarray, scipy.sparse.csr_matrix],
        factor_loc_totest: Union[str, List[str]] = None,
        coef_to_test: Union[str, List[str]] = None,
        formula_loc: str = None,
        formula_scale: str = "~1",
        as_numeric: Union[List[str], Tuple[str], str] = (),
        init_a: Union[np.ndarray, str] = "AUTO",
        init_b: Union[np.ndarray, str] = "AUTO",
        gene_names: Union[str, np.ndarray] = None,
        sample_description: pd.DataFrame = None,
        dmat_loc: Union[patsy.design_info.DesignMatrix, xr.Dataset] = None,
        dmat_scale: Union[patsy.design_info.DesignMatrix, xr.Dataset] = None,
        constraints_loc: np.ndarray = None,
        constraints_scale: np.ndarray = None,
        noise_model: str = "nb",
        size_factors: np.ndarray = None,
        batch_size: int = None,
        training_strategy: Union[str, List[Dict[str, object]], Callable] = "AUTO",
        quick_scale: bool = False,
        dtype="float64",
        **kwargs
):
    """
    Perform Wald test for differential expression for each gene.

    :param data: Array-like, xr.DataArray, xr.Dataset or anndata.Anndata object containing observations.
        Input data
    :param factor_loc_totest: str, list of strings
        List of factors of formula to test with Wald test.
        E.g. "condition" or ["batch", "condition"] if formula_loc would be "~ 1 + batch + condition"
    :param coef_to_test:
        If there are more than two groups specified by `factor_loc_totest`,
        this parameter allows to specify the group which should be tested.
        Alternatively, if factor_loc_totest is not given, this list sets
        the exact coefficients which are to be tested.
    :param formula: formula
        model formula for location and scale parameter models.
    :param formula_loc: formula
        model formula for location and scale parameter models.
        If not specified, `formula` will be used instead.
    :param formula_scale: formula
        model formula for scale parameter model.
        If not specified, `formula` will be used instead.
    :param as_numeric:
        Which columns of sample_description to treat as numeric and
        not as categorical. This yields columns in the design matrix
        which do not correpond to one-hot encoded discrete factors.
        This makes sense for number of genes, time, pseudotime or space
        for example.
    :param init_a: (Optional) Low-level initial values for a.
        Can be:

        - str:
            * "auto": automatically choose best initialization
            * "random": initialize with random values
            * "standard": initialize intercept with observed mean
            * "init_model": initialize with another model (see `ìnit_model` parameter)
            * "closed_form": try to initialize with closed form
        - np.ndarray: direct initialization of 'a'
    :param init_b: (Optional) Low-level initial values for b
        Can be:

        - str:
            * "auto": automatically choose best initialization
            * "random": initialize with random values
            * "standard": initialize with zeros
            * "init_model": initialize with another model (see `ìnit_model` parameter)
            * "closed_form": try to initialize with closed form
        - np.ndarray: direct initialization of 'b'
    :param gene_names: optional list/array of gene names which will be used if `data` does not implicitly store these
    :param sample_description: optional pandas.DataFrame containing sample annotations
    :param dmat_loc: Pre-built location model design matrix. 
        This over-rides formula_loc and sample description information given in
        data or sample_description. 
    :param dmat_scale: Pre-built scale model design matrix.
        This over-rides formula_scale and sample description information given in
        data or sample_description.
    :param constraints_loc: : Constraints for location model.
        Array with constraints in rows and model parameters in columns.
        Each constraint contains non-zero entries for the a of parameters that 
        has to sum to zero. This constraint is enforced by binding one parameter
        to the negative sum of the other parameters, effectively representing that
        parameter as a function of the other parameters. This dependent
        parameter is indicated by a -1 in this array, the independent parameters
        of that constraint (which may be dependent at an earlier constraint)
        are indicated by a 1. It is highly recommended to only use this option
        together with prebuilt design matrix for the location model, dmat_loc.
    :param constraints_scale: : Constraints for scale model.
        Array with constraints in rows and model parameters in columns.
        Each constraint contains non-zero entries for the a of parameters that 
        has to sum to zero. This constraint is enforced by binding one parameter
        to the negative sum of the other parameters, effectively representing that
        parameter as a function of the other parameters. This dependent
        parameter is indicated by a -1 in this array, the independent parameters
        of that constraint (which may be dependent at an earlier constraint)
        are indicated by a 1. It is highly recommended to only use this option
        together with prebuilt design matrix for the scale model, dmat_scale.
    :param size_factors: 1D array of transformed library size factors for each cell in the 
        same order as in data
    :param noise_model: str, noise model to use in model-based unit_test. Possible options:

        - 'nb': default
    :param batch_size: the batch size to use for the estimator
    :param training_strategy: {str, function, list} training strategy to use. Can be:

        - str: will use Estimator.TrainingStrategy[training_strategy] to train
        - function: Can be used to implement custom training function will be called as
          `training_strategy(estimator)`.
        - list of keyword dicts containing method arguments: Will call Estimator.train() once with each dict of
          method arguments.

          Example:

          .. code-block:: python

              [
                {"learning_rate": 0.5, },
                {"learning_rate": 0.05, },
              ]

          This will run training first with learning rate = 0.5 and then with learning rate = 0.05.
    :param quick_scale: Depending on the optimizer, `scale` will be fitted faster and maybe less accurate.

        Useful in scenarios where fitting the exact `scale` is not absolutely necessary.
    :param dtype: Allows specifying the precision which should be used to fit data.

        Should be "float32" for single precision or "float64" for double precision.
    :param kwargs: [Debugging] Additional arguments will be passed to the _fit method.
    """
    if len(kwargs) != 0:
        logger.debug("additional kwargs: %s", str(kwargs))

    if dmat_loc is None and formula_loc is None:
        raise ValueError("Supply either dmat_loc or formula_loc or formula.")
    if dmat_scale is None and formula_scale is None:
        raise ValueError("Supply either dmat_loc or formula_loc or formula.")
    # Check that factor_loc_totest and coef_to_test are lists and not single strings:
    if isinstance(factor_loc_totest, str):
        factor_loc_totest = [factor_loc_totest]
    if isinstance(coef_to_test, str):
        coef_to_test = [coef_to_test]
    if isinstance(as_numeric, str):
        as_numeric = [as_numeric]

    # # Parse input data formats:
    gene_names = _parse_gene_names(data, gene_names)
    X = _parse_data(data, gene_names)
    if dmat_loc is None and dmat_scale is None:
        sample_description = _parse_sample_description(data, sample_description)
    size_factors = _parse_size_factors(size_factors=size_factors, data=X)

    if dmat_loc is None:
        design_loc = data_utils.design_matrix(
            sample_description=sample_description,
            formula=formula_loc,
            as_categorical=[False if x in as_numeric else True for x in sample_description.columns.values]
        )
    else:
        design_loc = dmat_loc

    if dmat_scale is None:
        design_scale = data_utils.design_matrix(
            sample_description=sample_description,
            formula=formula_scale,
            as_categorical=[False if x in as_numeric else True for x in sample_description.columns.values]
        )
    else:
        design_scale = dmat_scale

    # Define indices of coefficients to test:
    contraints_loc_temp = constraints_loc if constraints_loc is not None else np.eye(design_loc.shape[-1])
    if factor_loc_totest is not None:
        # Select coefficients to test via formula model:
        col_indices = np.concatenate([
            np.arange(design_loc.shape[-1])[design_loc.design_info.slice(x)]
            for x in factor_loc_totest
        ])
        assert col_indices.size > 0, "Could not find any matching columns!"
        if coef_to_test is not None:
            if len(factor_loc_totest) > 1:
                raise ValueError("do not set coef_to_test if more than one factor_loc_totest is given")
            samples = sample_description[factor_loc_totest].astype(type(coef_to_test)) == coef_to_test
            one_cols = np.where(design_loc[samples][:, col_indices][0] == 1)
            if one_cols.size == 0:
                # there is no such column; modify design matrix to create one
                design_loc[:, col_indices] = np.where(samples, 1, 0)
    elif coef_to_test is not None:
        # Directly select coefficients to test from design matrix (xarray):
        # Check that coefficients to test are not dependent parameters if constraints are given:
        # TODO: design_loc is sometimes xarray and sometimes patsy when it arrives here,
        # should it not always be xarray?
        if isinstance(design_loc, patsy.design_info.DesignMatrix):
            col_indices = np.asarray([
                design_loc.design_info.column_names.index(x)
                for x in coef_to_test
            ])
        else:
            col_indices = np.asarray([
                list(np.asarray(design_loc.coords['design_params'])).index(x)
                for x in coef_to_test
            ])
    else:
        raise ValueError("either set factor_loc_totest or coef_to_test")
    # Check that all tested coefficients are independent:
    for x in col_indices:
        if np.sum(contraints_loc_temp[x,:]) != 1:
            raise ValueError("Constraints input is wrong: not all tested coefficients are unconstrained.")
    # Adjust tested coefficients from dependent to independent (fitted) parameters:
    col_indices = np.array([np.where(contraints_loc_temp[x,:] == 1)[0][0] for x in col_indices])

    ## Fit GLM:
    model = _fit(
        noise_model=noise_model,
        data=X,
        design_loc=design_loc,
        design_scale=design_scale,
        constraints_loc=constraints_loc,
        constraints_scale=constraints_scale,
        init_a=init_a,
        init_b=init_b,
        gene_names=gene_names,
        size_factors=size_factors,
        batch_size=batch_size,
        training_strategy=training_strategy,
        quick_scale=quick_scale,
        dtype=dtype,
        **kwargs,
    )

    ## Perform DE test:
    de_test = DifferentialExpressionTestWald(
        model_estim=model,
        col_indices=col_indices
    )

    return de_test


def _parse_grouping(data, sample_description, grouping):
    if isinstance(grouping, str):
        sample_description = _parse_sample_description(data, sample_description)
        grouping = sample_description[grouping]
    return np.squeeze(np.asarray(grouping))


def _split_X(data, grouping):
    groups = np.unique(grouping)
    x0 = data[np.where(grouping == groups[0])[0]]
    x1 = data[np.where(grouping == groups[1])[0]]
    return x0, x1


def t_test(
        data: Union[anndata.AnnData, anndata.base.Raw, xr.DataArray, xr.Dataset, np.ndarray, scipy.sparse.csr_matrix],
        grouping,
        gene_names=None,
        sample_description=None,
        dtype="float32"
):
    """
    Perform Welch's t-test for differential expression
    between two groups on adata object for each gene.

    :param data: Array-like, xr.DataArray, xr.Dataset or anndata.Anndata object containing observations.
        Input data
    :param grouping: str, array

        - column in data.obs/sample_description which contains the split of observations into the two groups.
        - array of length `num_observations` containing group labels
    :param gene_names: optional list/array of gene names which will be used if `data` does not implicitly store these
    :param sample_description: optional pandas.DataFrame containing sample annotations
    """
    gene_names = _parse_gene_names(data, gene_names)
    X = _parse_data(data, gene_names)
    if isinstance(X, SparseXArrayDataSet):
        X = X.X
    grouping = _parse_grouping(data, sample_description, grouping)

    de_test = DifferentialExpressionTestTT(
        data=X.astype(dtype),
        grouping=grouping,
        gene_names=gene_names,
    )

    return de_test


def rank_test(
        data: Union[anndata.AnnData, anndata.base.Raw, xr.DataArray, xr.Dataset, np.ndarray, scipy.sparse.csr_matrix],
        grouping,
        gene_names=None,
        sample_description=None,
        dtype="float32"
):
    """
    Perform Mann-Whitney rank test (Wilcoxon rank-sum test) for differential expression
    between two groups on adata object for each gene.

    :param data: Array-like, xr.DataArray, xr.Dataset or anndata.Anndata object containing observations.
        Input data
    :param grouping: str, array

        - column in data.obs/sample_description which contains the split of observations into the two groups.
        - array of length `num_observations` containing group labels
    :param gene_names: optional list/array of gene names which will be used if `data` does not implicitly store these
    :param sample_description: optional pandas.DataFrame containing sample annotations
    """
    gene_names = _parse_gene_names(data, gene_names)
    X = _parse_data(data, gene_names)
    if isinstance(X, SparseXArrayDataSet):
        X = X.X
    grouping = _parse_grouping(data, sample_description, grouping)

    de_test = DifferentialExpressionTestRank(
        data=X.astype(dtype),
        grouping=grouping,
        gene_names=gene_names,
    )

    return de_test


def two_sample(
        data: Union[anndata.AnnData, anndata.base.Raw, xr.DataArray, xr.Dataset, np.ndarray, scipy.sparse.csr_matrix],
        grouping: Union[str, np.ndarray, list],
        as_numeric: Union[List[str], Tuple[str], str] = (),
        test=None,
        gene_names=None,
        sample_description=None,
        noise_model: str = None,
        size_factors: np.ndarray = None,
        batch_size: int = None,
        training_strategy: Union[str, List[Dict[str, object]], Callable] = "AUTO",
        quick_scale: bool = None,
        dtype="float32",
        **kwargs
) -> _DifferentialExpressionTestSingle:
    r"""
    Perform differential expression test between two groups on adata object
    for each gene.

    This function wraps the selected statistical test for the scenario of
    a two sample comparison. All unit_test offered in this wrapper
    test for the difference of the mean parameter of both samples.
    The exact unit_test are as follows (assuming the group labels
    are saved in a column named "group"):

    - lrt(log-likelihood ratio test):
        Requires the fitting of 2 generalized linear models (full and reduced).
        The models are automatically assembled as follows, use the de.test.lrt()
        function if you would like to perform a different test.

            * full model location parameter: ~ 1 + group
            * full model scale parameter: ~ 1 + group
            * reduced model location parameter: ~ 1
            * reduced model scale parameter: ~ 1 + group
    - Wald test:
        Requires the fitting of 1 generalized linear models.
        model location parameter: ~ 1 + group
        model scale parameter: ~ 1 + group
        Test the group coefficient of the location parameter model against 0.
    - t-test:
        Doesn't require fitting of generalized linear models.
        Welch's t-test between both observation groups.
    - wilcoxon:
        Doesn't require fitting of generalized linear models.
        Wilcoxon rank sum (Mann-Whitney U) test between both observation groups.

    :param data: Array-like, xr.DataArray, xr.Dataset or anndata.Anndata object containing observations.
        Input data
    :param grouping: str, array

        - column in data.obs/sample_description which contains the split of observations into the two groups.
        - array of length `num_observations` containing group labels
    :param as_numeric:
        Which columns of sample_description to treat as numeric and
        not as categorical. This yields columns in the design matrix
        which do not correpond to one-hot encoded discrete factors.
        This makes sense for number of genes, time, pseudotime or space
        for example.
    :param test: str, statistical test to use. Possible options:

        - 'wald': default
        - 'lrt'
        - 't-test'
        - 'wilcoxon'
    :param gene_names: optional list/array of gene names which will be used if `data` does not implicitly store these
    :param sample_description: optional pandas.DataFrame containing sample annotations
    :param noise_model: str, noise model to use in model-based unit_test. Possible options:

        - 'nb': default
    :param size_factors: 1D array of transformed library size factors for each cell in the 
        same order as in data
    :param batch_size: the batch size to use for the estimator
    :param training_strategy: {str, function, list} training strategy to use. Can be:

        - str: will use Estimator.TrainingStrategy[training_strategy] to train
        - function: Can be used to implement custom training function will be called as
          `training_strategy(estimator)`.
        - list of keyword dicts containing method arguments: Will call Estimator.train() once with each dict of
          method arguments.

          Example:

          .. code-block:: python

              [
                {"learning_rate": 0.5, },
                {"learning_rate": 0.05, },
              ]

          This will run training first with learning rate = 0.5 and then with learning rate = 0.05.
    :param quick_scale: Depending on the optimizer, `scale` will be fitted faster and maybe less accurate.

        Useful in scenarios where fitting the exact `scale` is not absolutely necessary.
    :param dtype: Allows specifying the precision which should be used to fit data.

        Should be "float32" for single precision or "float64" for double precision.
    :param kwargs: [Debugging] Additional arguments will be passed to the _fit method.
    """
    if test in ['t-test', 'wilcoxon'] and noise_model is not None:
        raise ValueError('base.two_sample(): Do not specify `noise_model` if using test t-test or wilcoxon: ' +
                         'The t-test is based on a gaussian noise model and wilcoxon is model free.')

    gene_names = _parse_gene_names(data, gene_names)
    X = _parse_data(data, gene_names)
    grouping = _parse_grouping(data, sample_description, grouping)
    sample_description = pd.DataFrame({"grouping": grouping})

    groups = np.unique(grouping)
    if groups.size > 2:
        raise ValueError("More than two groups detected:\n\t%s", groups)
    if groups.size < 2:
        raise ValueError("Less than two groups detected:\n\t%s", groups)

    # Set default test:
    if test is None:
        test = 'wald'

    if test.lower() == 'wald':
        if noise_model is None:
            raise ValueError("Please specify noise_model")
        formula_loc = '~ 1 + grouping'
        formula_scale = '~ 1 + grouping'
        de_test = wald(
            data=X,
            factor_loc_totest="grouping",
            as_numeric=as_numeric,
            coef_to_test=None,
            formula_loc=formula_loc,
            formula_scale=formula_scale,
            gene_names=gene_names,
            sample_description=sample_description,
            noise_model=noise_model,
            size_factors=size_factors,
            batch_size=batch_size,
            training_strategy=training_strategy,
            quick_scale=quick_scale,
            dtype=dtype,
            **kwargs
        )
    elif test.lower() == 'lrt':
        if noise_model is None:
            raise ValueError("Please specify noise_model")
        full_formula_loc = '~ 1 + grouping'
        full_formula_scale = '~ 1 + grouping'
        reduced_formula_loc = '~ 1'
        reduced_formula_scale = '~ 1 + grouping'
        de_test = lrt(
            data=X,
            full_formula_loc=full_formula_loc,
            reduced_formula_loc=reduced_formula_loc,
            full_formula_scale=full_formula_scale,
            reduced_formula_scale=reduced_formula_scale,
            as_numeric=as_numeric,
            gene_names=gene_names,
            sample_description=sample_description,
            noise_model=noise_model,
            size_factors=size_factors,
            batch_size=batch_size,
            training_strategy=training_strategy,
            quick_scale=quick_scale,
            dtype=dtype,
            **kwargs
        )
    elif test.lower() == 't-test' or test.lower() == "t_test" or test.lower() == "ttest":
        de_test = t_test(
            data=X,
            gene_names=gene_names,
            grouping=grouping,
            dtype=dtype
        )
    elif test.lower() == 'wilcoxon':
        de_test = rank_test(
            data=X,
            gene_names=gene_names,
            grouping=grouping,
            dtype=dtype
        )
    else:
        raise ValueError('base.two_sample(): Parameter `test="%s"` not recognized.' % test)

    return de_test


def pairwise(
        data: Union[anndata.AnnData, anndata.base.Raw, xr.DataArray, xr.Dataset, np.ndarray, scipy.sparse.csr_matrix],
        grouping: Union[str, np.ndarray, list],
        as_numeric: Union[List[str], Tuple[str], str] = [],
        test: str = 'z-test',
        lazy: bool = False,
        gene_names: str = None,
        sample_description: pd.DataFrame = None,
        noise_model: str = None,
        pval_correction: str = "global",
        size_factors: np.ndarray = None,
        batch_size: int = None,
        training_strategy: Union[str, List[Dict[str, object]], Callable] = "AUTO",
        quick_scale: bool = None,
        dtype="float32",
        keep_full_test_objs: bool = False,
        **kwargs
):
    """
    Perform pairwise differential expression test between two groups on adata object
    for each gene for all combinations of pairs of groups.

    This function wraps the selected statistical test for the scenario of
    a two sample comparison. All unit_test offered in this wrapper
    test for the difference of the mean parameter of both samples. We note
    that the much more efficient default method is coefficient based
    and only requires one model fit.

    The exact unit_test are as follows (assuming the group labels
    are saved in a column named "group"), each test is executed
    on the subset of the data that only contains observations of a given
    pair of groups:

    - lrt(log-likelihood ratio test):
        Requires the fitting of 2 generalized linear models (full and reduced).

        * full model location parameter: ~ 1 + group
        * full model scale parameter: ~ 1 + group
        * reduced model location parameter: ~ 1
        * reduced model scale parameter: ~ 1 + group
    - Wald test:
        Requires the fitting of 1 generalized linear models.
        model location parameter: ~ 1 + group
        model scale parameter: ~ 1 + group
        Test the group coefficient of the location parameter model against 0.
    - t-test:
        Doesn't require fitting of generalized linear models.
        Welch's t-test between both observation groups.
    - wilcoxon:
        Doesn't require fitting of generalized linear models.
        Wilcoxon rank sum (Mann-Whitney U) test between both observation groups.

    :param data: Array-like, xr.DataArray, xr.Dataset or anndata.Anndata object containing observations.
        Input data
    :param grouping: str, array

        - column in data.obs/sample_description which contains the split of observations into the two groups.
        - array of length `num_observations` containing group labels
    :param as_numeric:
        Which columns of sample_description to treat as numeric and
        not as categorical. This yields columns in the design matrix
        which do not correpond to one-hot encoded discrete factors.
        This makes sense for number of genes, time, pseudotime or space
        for example.
    :param test: str, statistical test to use. Possible options:

        - 'z-test': default
        - 'wald'
        - 'lrt'
        - 't-test'
        - 'wilcoxon'
    :param lazy: bool, whether to enable lazy results evaluation.
        This is only possible if test=="ztest" and yields an output object which computes
        p-values etc. only upon request of certain pairs. This makes sense if the entire
        gene x groups x groups matrix which contains all pairwise p-values, q-values or
        log-fold changes is very large and may not fit into memory, especially if only
        a certain subset of the pairwise comparisons is desired anyway.
    :param gene_names: optional list/array of gene names which will be used if `data` does not implicitly store these
    :param sample_description: optional pandas.DataFrame containing sample annotations
    :param noise_model: str, noise model to use in model-based unit_test. Possible options:

        - 'nb': default
    :param pval_correction: Choose between global and test-wise correction.
        Can be:

        - "global": correct all p-values in one operation
        - "by_test": correct the p-values of each test individually
    :param size_factors: 1D array of transformed library size factors for each cell in the 
        same order as in data
    :param batch_size: the batch size to use for the estimator
    :param training_strategy: {str, function, list} training strategy to use. Can be:

        - str: will use Estimator.TrainingStrategy[training_strategy] to train
        - function: Can be used to implement custom training function will be called as
          `training_strategy(estimator)`.
        - list of keyword dicts containing method arguments: Will call Estimator.train() once with each dict of
          method arguments.

          Example:

          .. code-block:: python

              [
                {"learning_rate": 0.5, },
                {"learning_rate": 0.05, },
              ]

          This will run training first with learning rate = 0.5 and then with learning rate = 0.05.
    :param quick_scale: Depending on the optimizer, `scale` will be fitted faster and maybe less accurate.

        Useful in scenarios where fitting the exact `scale` is not absolutely necessary.
    :param dtype: Allows specifying the precision which should be used to fit data.

        Should be "float32" for single precision or "float64" for double precision.
    :param keep_full_test_objs: [Debugging] keep the individual test objects; currently valid for test != "z-test"
    :param kwargs: [Debugging] Additional arguments will be passed to the _fit method.
    """
    if len(kwargs) != 0:
        logger.info("additional kwargs: %s", str(kwargs))

    if lazy and not (test.lower() == 'z-test' or test.lower() == 'z_test' or test.lower() == 'ztest'):
        raise ValueError("lazy evaluation of pairwise tests only possible if test is z-test")

    # Do not store all models but only p-value and q-value matrix:
    # genes x groups x groups
    gene_names = _parse_gene_names(data, gene_names)
    X = _parse_data(data, gene_names)
    sample_description = _parse_sample_description(data, sample_description)
    grouping = _parse_grouping(data, sample_description, grouping)
    sample_description = pd.DataFrame({"grouping": grouping})

    if test.lower() == 'z-test' or test.lower() == 'z_test' or test.lower() == 'ztest':
        # -1 in formula removes intercept
        dmat = data_utils.design_matrix(
            sample_description,
            formula="~ 1 - 1 + grouping"
        )
        model = _fit(
            noise_model=noise_model,
            data=X,
            design_loc=dmat,
            design_scale=dmat,
            gene_names=gene_names,
            size_factors=size_factors,
            batch_size=batch_size,
            training_strategy=training_strategy,
            quick_scale=quick_scale,
            dtype=dtype,
            **kwargs
        )

        if lazy:
            de_test = DifferentialExpressionTestZTestLazy(
                model_estim=model,
                grouping=grouping,
                groups=np.unique(grouping),
                correction_type=pval_correction
            )
        else:
            de_test = DifferentialExpressionTestZTest(
                model_estim=model,
                grouping=grouping,
                groups=np.unique(grouping),
                correction_type=pval_correction
            )
    else:
        groups = np.unique(grouping)
        pvals = np.tile(np.NaN, [len(groups), len(groups), X.shape[1]])
        pvals[np.eye(pvals.shape[0]).astype(bool)] = 0
        logfc = np.tile(np.NaN, [len(groups), len(groups), X.shape[1]])
        logfc[np.eye(logfc.shape[0]).astype(bool)] = 0

        if keep_full_test_objs:
            tests = np.tile([None], [len(groups), len(groups)])
        else:
            tests = None

        for i, g1 in enumerate(groups):
            for j, g2 in enumerate(groups[(i + 1):]):
                j = j + i + 1

                sel = (grouping == g1) | (grouping == g2)
                de_test_temp = two_sample(
                    data=X[sel],
                    grouping=grouping[sel],
                    as_numeric=as_numeric,
                    test=test,
                    gene_names=gene_names,
                    sample_description=sample_description.iloc[sel],
                    noise_model=noise_model,
                    size_factors=size_factors[sel] if size_factors is not None else None,
                    batch_size=batch_size,
                    training_strategy=training_strategy,
                    quick_scale=quick_scale,
                    dtype=dtype,
                    **kwargs
                )
                pvals[i, j] = de_test_temp.pval
                pvals[j, i] = pvals[i, j]
                logfc[i, j] = de_test_temp.log_fold_change()
                logfc[j, i] = - logfc[i, j]
                if keep_full_test_objs:
                    tests[i, j] = de_test_temp
                    tests[j, i] = de_test_temp

        de_test = DifferentialExpressionTestPairwise(
            gene_ids=gene_names,
            pval=pvals,
            logfc=logfc,
            ave=np.mean(X, axis=0),
            groups=groups,
            tests=tests,
            correction_type=pval_correction
        )

    return de_test


def versus_rest(
        data: Union[anndata.AnnData, anndata.base.Raw, xr.DataArray, xr.Dataset, np.ndarray, scipy.sparse.csr_matrix],
        grouping: Union[str, np.ndarray, list],
        as_numeric: Union[List[str], Tuple[str], str] = (),
        test: str = 'wald',
        gene_names: str = None,
        sample_description: pd.DataFrame = None,
        noise_model: str = None,
        pval_correction: str = "global",
        size_factors: np.ndarray = None,
        batch_size: int = None,
        training_strategy: Union[str, List[Dict[str, object]], Callable] = "AUTO",
        quick_scale: bool = None,
        dtype="float32",
        keep_full_test_objs: bool = False,
        **kwargs
):
    """
    Perform pairwise differential expression test between two groups on adata object
    for each gene for each groups versus the rest of the data set.

    This function wraps the selected statistical test for the scenario of
    a two sample comparison. All unit_test offered in this wrapper
    test for the difference of the mean parameter of both samples. We note
    that the much more efficient default method is coefficient based
    and only requires one model fit.

    The exact unit_test are as follows (assuming the group labels
    are saved in a column named "group"), each test is executed
    on the entire data and the labels are modified so that the target group
    is one group and the remaining groups are allocated to the second reference
    group):

    - lrt(log-likelihood ratio test):
        Requires the fitting of 2 generalized linear models (full and reduced).

        * full model location parameter: ~ 1 + group
        * full model scale parameter: ~ 1 + group
        * reduced model location parameter: ~ 1
        * reduced model scale parameter: ~ 1 + group
    - Wald test:
        Requires the fitting of 1 generalized linear models.
        model location parameter: ~ 1 + group
        model scale parameter: ~ 1 + group
        Test the group coefficient of the location parameter model against 0.
    - t-test:
        Doesn't require fitting of generalized linear models.
        Welch's t-test between both observation groups.
    - wilcoxon:
        Doesn't require fitting of generalized linear models.
        Wilcoxon rank sum (Mann-Whitney U) test between both observation groups.

    :param data: Array-like, xr.DataArray, xr.Dataset or anndata.Anndata object containing observations.
        Input data
    :param grouping: str, array

        - column in data.obs/sample_description which contains the split of observations into the two groups.
        - array of length `num_observations` containing group labels
    :param as_numeric:
        Which columns of sample_description to treat as numeric and
        not as categorical. This yields columns in the design matrix
        which do not correpond to one-hot encoded discrete factors.
        This makes sense for number of genes, time, pseudotime or space
        for example.
    :param test: str, statistical test to use. Possible options:

        - 'wald'
        - 'lrt'
        - 't-test'
        - 'wilcoxon'
    :param gene_names: optional list/array of gene names which will be used if `data` does not implicitly store these
    :param sample_description: optional pandas.DataFrame containing sample annotations
    :param noise_model: str, noise model to use in model-based unit_test. Possible options:

        - 'nb': default
    :param pval_correction: Choose between global and test-wise correction.
        Can be:

        - "global": correct all p-values in one operation
        - "by_test": correct the p-values of each test individually
    :param size_factors: 1D array of transformed library size factors for each cell in the 
        same order as in data
    :param batch_size: the batch size to use for the estimator
    :param training_strategy: {str, function, list} training strategy to use. Can be:

        - str: will use Estimator.TrainingStrategy[training_strategy] to train
        - function: Can be used to implement custom training function will be called as
          `training_strategy(estimator)`.
        - list of keyword dicts containing method arguments: Will call Estimator.train() once with each dict of
          method arguments.

          Example:

          .. code-block:: python

              [
                {"learning_rate": 0.5, },
                {"learning_rate": 0.05, },
              ]

          This will run training first with learning rate = 0.5 and then with learning rate = 0.05.
    :param quick_scale: Depending on the optimizer, `scale` will be fitted faster and maybe less accurate.

        Useful in scenarios where fitting the exact `scale` is not
    :param dtype: Allows specifying the precision which should be used to fit data.

        Should be "float32" for single precision or "float64" for double precision.
    :param keep_full_test_objs: [Debugging] keep the individual test objects; currently valid for test != "z-test"
    :param kwargs: [Debugging] Additional arguments will be passed to the _fit method.
    """
    if len(kwargs) != 0:
        logger.info("additional kwargs: %s", str(kwargs))

    # Do not store all models but only p-value and q-value matrix:
    # genes x groups
    gene_names = _parse_gene_names(data, gene_names)
    X = _parse_data(data, gene_names)
    sample_description = _parse_sample_description(data, sample_description)
    grouping = _parse_grouping(data, sample_description, grouping)
    sample_description = pd.DataFrame({"grouping": grouping})

    groups = np.unique(grouping)
    pvals = np.zeros([1, len(groups), X.shape[1]])
    logfc = np.zeros([1, len(groups), X.shape[1]])

    if keep_full_test_objs:
        tests = np.tile([None], [1, len(groups)])
    else:
        tests = None

    for i, g1 in enumerate(groups):
        test_grouping = np.where(grouping == g1, "group", "rest")
        de_test_temp = two_sample(
            data=X,
            grouping=test_grouping,
            as_numeric=as_numeric,
            test=test,
            gene_names=gene_names,
            sample_description=sample_description,
            noise_model=noise_model,
            batch_size=batch_size,
            training_strategy=training_strategy,
            quick_scale=quick_scale,
            size_factors=size_factors,
            dtype=dtype,
            **kwargs
        )
        pvals[0, i] = de_test_temp.pval
        logfc[0, i] = de_test_temp.log_fold_change()
        if keep_full_test_objs:
            tests[0, i] = de_test_temp

    de_test = DifferentialExpressionTestVsRest(
        gene_ids=gene_names,
        pval=pvals,
        logfc=logfc,
        ave=np.mean(X, axis=0),
        groups=groups,
        tests=tests,
        correction_type=pval_correction
    )

    return de_test


def partition(
        data: Union[anndata.AnnData, anndata.base.Raw, xr.DataArray, xr.Dataset, np.ndarray, scipy.sparse.csr_matrix],
        partition: Union[str, np.ndarray, list],
        gene_names: str = None,
        sample_description: pd.DataFrame = None):
    r"""
    Perform differential expression test for each group. This class handles
    the partitioning of the data set, the differential test callls and
    the sumamry of the individual tests into one
    DifferentialExpressionTestMulti object. All functions the yield
    DifferentialExpressionTestSingle objects can be performed on each
    partition.

    Wraps _Partition so that doc strings are nice.

    :param data: Array-like, xr.DataArray, xr.Dataset or anndata.Anndata object containing observations.
        Input data
    :param gene_names: optional list/array of gene names which will be used if `data` does not implicitly store these
    :param sample_description: optional pandas.DataFrame containing sample annotations
    """
    return (_Partition(
        data=data,
        partition=partition,
        gene_names=gene_names,
        sample_description=sample_description))


class _Partition():
    """
    Perform differential expression test for each group. This class handles
    the partitioning of the data set, the differential test callls and
    the sumamry of the individual tests into one
    DifferentialExpressionTestMulti object. All functions the yield
    DifferentialExpressionTestSingle objects can be performed on each
    partition.
    """

    def __init__(
            self,
            data: Union[anndata.AnnData, xr.DataArray, xr.Dataset, np.ndarray],
            partition: Union[str, np.ndarray, list],
            gene_names: str = None,
            sample_description: pd.DataFrame = None):
        """
        :param data: Array-like, xr.DataArray, xr.Dataset or anndata.Anndata object containing observations.
        Input data
        :param partition: str, array

            - column in data.obs/sample_description which contains the split of observations into the two groups.
            - array of length `num_observations` containing group labels
        :param gene_names: optional list/array of gene names which will be used if `data` does not implicitly store these
        :param sample_description: optional pandas.DataFrame containing sample annotations
        """
        self.X = _parse_data(data, gene_names)
        self.gene_names = _parse_gene_names(data, gene_names)
        self.sample_description = _parse_sample_description(data, sample_description)
        self.partition = _parse_grouping(data, sample_description, partition)
        self.partitions = np.unique(self.partition)
        self.partition_idx = [np.where(self.partition == x)[0] for x in self.partitions]

    def two_sample(
            self,
            grouping: Union[str],
            as_numeric: Union[List[str], Tuple[str], str] = (),
            test=None,
            noise_model: str = None,
            size_factors: np.ndarray = None,
            batch_size: int = None,
            training_strategy: Union[str, List[Dict[str, object]], Callable] = "AUTO",
            **kwargs
    ) -> _DifferentialExpressionTestMulti:
        """
        See annotation of de.test.two_sample()

        :param grouping: str

            - column in data.obs/sample_description which contains the split of observations into the two groups.
        :param as_numeric:
            Which columns of sample_description to treat as numeric and
            not as categorical. This yields columns in the design matrix
            which do not correpond to one-hot encoded discrete factors.
            This makes sense for number of genes, time, pseudotime or space
            for example.
        :param test: str, statistical test to use. Possible options:

            - 'wald': default
            - 'lrt'
            - 't-test'
            - 'wilcoxon'
        :param noise_model: str, noise model to use in model-based unit_test. Possible options:

            - 'nb': default
        :param batch_size: the batch size to use for the estimator
        :param training_strategy: {str, function, list} training strategy to use. Can be:

            - str: will use Estimator.TrainingStrategy[training_strategy] to train
            - function: Can be used to implement custom training function will be called as
              `training_strategy(estimator)`.
            - list of keyword dicts containing method arguments: Will call Estimator.train() once with each dict of
              method arguments.

              Example:

              .. code-block:: python

                  [
                    {"learning_rate": 0.5, },
                    {"learning_rate": 0.05, },
                  ]

              This will run training first with learning rate = 0.5 and then with learning rate = 0.05.
        :param kwargs: [Debugging] Additional arguments will be passed to the _fit method.
        """
        DETestsSingle = []
        for i, idx in enumerate(self.partition_idx):
            DETestsSingle.append(two_sample(
                data=self.X[idx, :],
                grouping=grouping,
                as_numeric=as_numeric,
                test=test,
                gene_names=self.gene_names,
                sample_description=self.sample_description.iloc[idx, :],
                noise_model=noise_model,
                size_factors=size_factors[idx] if size_factors is not None else None,
                batch_size=batch_size,
                training_strategy=training_strategy,
                **kwargs
            ))
        return DifferentialExpressionTestByPartition(
            partitions=self.partitions,
            tests=DETestsSingle,
            ave=np.mean(self.X, axis=0),
            correction_type="by_test")

    def t_test(
            self,
            grouping: Union[str],
            dtype="float32"
    ):
        """
        See annotation of de.test.t_test()

        :param grouping: str

            - column in data.obs/sample_description which contains the split of observations into the two groups.
        """
        DETestsSingle = []
        for i, idx in enumerate(self.partition_idx):
            DETestsSingle.append(t_test(
                data=self.X[idx, :],
                grouping=grouping,
                gene_names=self.gene_names,
                sample_description=self.sample_description.iloc[idx, :],
                dtype=dtype
            ))
        return DifferentialExpressionTestByPartition(
            partitions=self.partitions,
            tests=DETestsSingle,
            ave=np.mean(self.X, axis=0),
            correction_type="by_test")

    def wilcoxon(
            self,
            grouping: Union[str],
            dtype="float32"
    ):
        """
        See annotation of de.test.wilcoxon()

        :param grouping: str, array

            - column in data.obs/sample_description which contains the split of observations into the two groups.
            - array of length `num_observations` containing group labels
        """
        DETestsSingle = []
        for i, idx in enumerate(self.partition_idx):
            DETestsSingle.append(rank_test(
                data=self.X[idx, :],
                grouping=grouping,
                gene_names=self.gene_names,
                sample_description=self.sample_description.iloc[idx, :],
                dtype=dtype
            ))
        return DifferentialExpressionTestByPartition(
            partitions=self.partitions,
            tests=DETestsSingle,
            ave=np.mean(self.X, axis=0),
            correction_type="by_test")

    def lrt(
            self,
            reduced_formula_loc: str = None,
            full_formula_loc: str = None,
            reduced_formula_scale: str = None,
            full_formula_scale: str = None,
            as_numeric: Union[List[str], Tuple[str], str] = (),
            noise_model="nb",
            size_factors: np.ndarray = None,
            batch_size: int = None,
            training_strategy: Union[str, List[Dict[str, object]], Callable] = "AUTO",
            **kwargs
    ):
        """
        See annotation of de.test.lrt()

        :param reduced_formula_loc: formula
            Reduced model formula for location and scale parameter models.
            If not specified, `reduced_formula` will be used instead.
        :param full_formula_loc: formula
            Full model formula for location parameter model.
            If not specified, `full_formula` will be used instead.
        :param reduced_formula_scale: formula
            Reduced model formula for scale parameter model.
            If not specified, `reduced_formula` will be used instead.
        :param full_formula_scale: formula
            Full model formula for scale parameter model.
            If not specified, `reduced_formula_scale` will be used instead.
        :param as_numeric:
            Which columns of sample_description to treat as numeric and
            not as categorical. This yields columns in the design matrix
            which do not correpond to one-hot encoded discrete factors.
            This makes sense for number of genes, time, pseudotime or space
            for example.
        :param noise_model: str, noise model to use in model-based unit_test. Possible options:

            - 'nb': default
        :param size_factors: 1D array of transformed library size factors for each cell in the 
            same order as in data
        :param batch_size: the batch size to use for the estimator
        :param training_strategy: {str, function, list} training strategy to use. Can be:

            - str: will use Estimator.TrainingStrategy[training_strategy] to train
            - function: Can be used to implement custom training function will be called as
              `training_strategy(estimator)`.
            - list of keyword dicts containing method arguments: Will call Estimator.train() once with each dict of
              method arguments.

              Example:

              .. code-block:: python

                  [
                    {"learning_rate": 0.5, },
                    {"learning_rate": 0.05, },
                  ]

              This will run training first with learning rate = 0.5 and then with learning rate = 0.05.
        :param kwargs: [Debugging] Additional arguments will be passed to the _fit method.
        """
        DETestsSingle = []
        for i, idx in enumerate(self.partition_idx):
            DETestsSingle.append(lrt(
                data=self.X[idx, :],
                reduced_formula_loc=reduced_formula_loc,
                full_formula_loc=full_formula_loc,
                reduced_formula_scale=reduced_formula_scale,
                full_formula_scale=full_formula_scale,
                gene_names=self.gene_names,
                sample_description=self.sample_description.iloc[idx, :],
                noise_model=noise_model,
                size_factors=size_factors[idx] if size_factors is not None else None,
                batch_size=batch_size,
                training_strategy=training_strategy,
                **kwargs
            ))
        return DifferentialExpressionTestByPartition(
            partitions=self.partitions,
            tests=DETestsSingle,
            ave=np.mean(self.X, axis=0),
            correction_type="by_test")

    def wald(
            self,
            factor_loc_totest: str,
            coef_to_test: object = None,  # e.g. coef_to_test="B"
            formula_loc: str = None,
            formula_scale: str = None,
            as_numeric: Union[List[str], Tuple[str], str] = (),
            noise_model: str = "nb",
            size_factors: np.ndarray = None,
            batch_size: int = None,
            training_strategy: Union[str, List[Dict[str, object]], Callable] = "AUTO",
            **kwargs
    ):
        """
        This function performs a wald test within each partition of a data set.
        See annotation of de.test.wald()

        :param formula_loc: formula
            model formula for location and scale parameter models.
            If not specified, `formula` will be used instead.
        :param formula_scale: formula
            model formula for scale parameter model.
            If not specified, `formula` will be used instead.
        :param factor_loc_totest: str
            Factor of formula to test with Wald test.
            E.g. "condition" if formula_loc would be "~ 1 + batch + condition"
        :param coef_to_test: If there are more than two groups specified by `factor_loc_totest`,
            this parameter allows to specify the group which should be tested
        :param as_numeric:
            Which columns of sample_description to treat as numeric and
            not as categorical. This yields columns in the design matrix
            which do not correpond to one-hot encoded discrete factors.
            This makes sense for number of genes, time, pseudotime or space
            for example.
        :param noise_model: str, noise model to use in model-based unit_test. Possible options:

            - 'nb': default
        :param size_factors: 1D array of transformed library size factors for each cell in the 
            same order as in data
        :param batch_size: the batch size to use for the estimator
        :param training_strategy: {str, function, list} training strategy to use. Can be:

            - str: will use Estimator.TrainingStrategy[training_strategy] to train
            - function: Can be used to implement custom training function will be called as
              `training_strategy(estimator)`.
            - list of keyword dicts containing method arguments: Will call Estimator.train() once with each dict of
              method arguments.

              Example:

              .. code-block:: python

                  [
                    {"learning_rate": 0.5, },
                    {"learning_rate": 0.05, },
                  ]

              This will run training first with learning rate = 0.5 and then with learning rate = 0.05.
        :param kwargs: [Debugging] Additional arguments will be passed to the _fit method.
        """
        DETestsSingle = []
        for i, idx in enumerate(self.partition_idx):
            DETestsSingle.append(wald(
                data=self.X[idx, :],
                factor_loc_totest=factor_loc_totest,
                coef_to_test=coef_to_test,
                formula_loc=formula_loc,
                formula_scale=formula_scale,
                as_numeric=as_numeric,
                gene_names=self.gene_names,
                sample_description=self.sample_description.iloc[idx, :],
                noise_model=noise_model,
                size_factors=size_factors[idx] if size_factors is not None else None,
                batch_size=batch_size,
                training_strategy=training_strategy,
                **kwargs
            ))
        return DifferentialExpressionTestByPartition(
            partitions=self.partitions,
            tests=DETestsSingle,
            ave=np.mean(self.X, axis=0),
            correction_type="by_test")


def continuous_1d(
        data: Union[anndata.AnnData, anndata.base.Raw, xr.DataArray, xr.Dataset, np.ndarray, scipy.sparse.csr_matrix],
        continuous: str,
        df: int = 5,
        factor_loc_totest: Union[str, List[str]] = None,
        formula: str = None,
        formula_loc: str = None,
        formula_scale: str = None,
        as_numeric: Union[List[str], Tuple[str], str] = (),
        test: str = 'wald',
        init_a: Union[np.ndarray, str] = "standard",
        init_b: Union[np.ndarray, str] = "standard",
        gene_names=None,
        sample_description=None,
        noise_model: str = 'nb',
        size_factors: np.ndarray = None,
        batch_size: int = None,
        training_strategy: Union[str, List[Dict[str, object]], Callable] = "DEFAULT",
        quick_scale: bool = None,
        dtype="float64",
        **kwargs
) -> _DifferentialExpressionTestSingle:
    r"""
    Perform differential expression along continous covariate.

    This function wraps the selected statistical test for
    scenarios with continuous covariates and performs the necessary
    spline basis transformation of the continuous covariate so that the
    problem can be framed as a GLM.

    Note that direct supply of dmats is not enabled as this function wraps
    the building of an adjusted design matrix which contains the spline basis
    covariates. Advanced users who want to control dmat can directly
    perform these spline basis transforms outside of diffxpy and feed the
    dmat directly to one of the test routines wald() or lrt().

    :param data: Array-like, xr.DataArray, xr.Dataset or anndata.Anndata object containing observations.
        Input data
    :param continuous: str

        - column in data.obs/sample_description which contains the continuous covariate.
    :param df: int
        Degrees of freedom of the spline model, i.e. the number of spline basis vectors.
        df is equal to the number of coefficients in the GLM which are used to describe the
        continuous depedency-
    :param factor_loc_totest:
        List of factors of formula to test with Wald test.
        E.g. "condition" or ["batch", "condition"] if formula_loc would be "~ 1 + batch + condition"
    :param formula: formula
        Model formula for location and scale parameter models.
        Refer to continuous covariate by the name givne in the parameter continuous,
        this will be propagated across all coefficients which represent this covariate
        in the spline basis space.
    :param formula_loc: formula
        Model formula for location and scale parameter models.
        If not specified, `formula` will be used instead.
        Refer to continuous covariate by the name givne in the parameter continuous,
        this will be propagated across all coefficients which represent this covariate
        in the spline basis space.
    :param formula_scale: formula
        model formula for scale parameter model.
        If not specified, `formula` will be used instead.
        Refer to continuous covariate by the name givne in the parameter continuous,
        this will be propagated across all coefficients which represent this covariate
        in the spline basis space.
    :param as_numeric:
        Which columns of sample_description to treat as numeric and
        not as categorical. This yields columns in the design matrix
        which do not correpond to one-hot encoded discrete factors.
        This makes sense for number of genes, time, pseudotime or space
        for example.
    :param test: str, statistical test to use. Possible options:

        - 'wald': default
        - 'lrt'
    :param init_a: (Optional) Low-level initial values for a.
        Can be:

        - str:
            * "auto": automatically choose best initialization
            * "random": initialize with random values
            * "standard": initialize intercept with observed mean
        - np.ndarray: direct initialization of 'a'
    :param init_b: (Optional) Low-level initial values for b
        Can be:

        - str:
            * "auto": automatically choose best initialization
            * "random": initialize with random values
            * "standard": initialize with zeros
        - np.ndarray: direct initialization of 'b'
    :param gene_names: optional list/array of gene names which will be used if `data` does not implicitly store these
    :param sample_description: optional pandas.DataFrame containing sample annotations
    :param noise_model: str, noise model to use in model-based unit_test. Possible options:

        - 'nb': default
    :param size_factors: 1D array of transformed library size factors for each cell in the
        same order as in data
    :param batch_size: the batch size to use for the estimator
    :param training_strategy: {str, function, list} training strategy to use. Can be:

        - str: will use Estimator.TrainingStrategy[training_strategy] to train
        - function: Can be used to implement custom training function will be called as
          `training_strategy(estimator)`.
        - list of keyword dicts containing method arguments: Will call Estimator.train() once with each dict of
          method arguments.

          Example:

          .. code-block:: python

              [
                {"learning_rate": 0.5, },
                {"learning_rate": 0.05, },
              ]

          This will run training first with learning rate = 0.5 and then with learning rate = 0.05.
    :param quick_scale: Depending on the optimizer, `scale` will be fitted faster and maybe less accurate.

        Useful in scenarios where fitting the exact `scale` is not absolutely necessary.
    :param dtype: Allows specifying the precision which should be used to fit data.

        Should be "float32" for single precision or "float64" for double precision.
    :param kwargs: [Debugging] Additional arguments will be passed to the _fit method.
    """
    if formula is None and (formula_loc is None or formula_scale is None):
        raise ValueError("supply either formula or fomula_loc and formula_scale")
    if formula is not None and (formula_loc is not None or formula_scale is not None):
        raise ValueError("supply either formula or fomula_loc and formula_scale")
    # Check that continuous factor is contained in model formulas:
    if formula is not None:
        pass
    # Set testing default to continuous covariate if not supplied:
    if factor_loc_totest is None:
        factor_loc_totest = [continuous]
    elif isinstance(factor_loc_totest, str):
        factor_loc_totest = [factor_loc_totest]
    elif isinstance(factor_loc_totest, tuple):
        factor_loc_totest = list(factor_loc_totest)

    if isinstance(as_numeric, str):
        as_numeric = [as_numeric]
    if isinstance(as_numeric, tuple):
        as_numeric = list(as_numeric)

    X = _parse_data(data, gene_names)
    gene_names = _parse_gene_names(data, gene_names)
    sample_description = _parse_sample_description(data, sample_description)

    # Check that continuous factor is contained in sample description
    if continuous not in sample_description.columns:
        raise ValueError('parameter continuous not found in sample_description')

    # Perform spline basis transform.
    spline_basis = patsy.highlevel.dmatrix("0+bs(" + continuous + ", df=" + str(df) + ")", sample_description)
    spline_basis = pd.DataFrame(spline_basis)
    new_coefs = [continuous + str(i) for i in range(spline_basis.shape[1])]
    spline_basis.columns = new_coefs
    formula_extension = '+'.join(new_coefs)

    # Replace continuous factor in formulas by spline basis coefficients.
    # Note that the brackets around formula_term_continuous propagate the sum
    # across interaction terms.
    formula_term_continuous = '(' + formula_extension + ')'

    if formula_loc is not None:
        formula_loc_new = formula_loc.split(continuous)
        formula_loc_new = formula_term_continuous.join(formula_loc_new)
    else:
        formula_loc_new = None

    if formula_scale is not None:
        formula_scale_new = formula_scale.split(continuous)
        formula_scale_new = formula_term_continuous.join(formula_scale_new)
    else:
        formula_scale_new = None

    # Add spline basis into sample description
    for x in spline_basis.columns:
        sample_description[x] = spline_basis[x].values

    # Add spline basis to continuous covariate list
    as_numeric.extend(new_coefs)

    if test.lower() == 'wald':
        if noise_model is None:
            raise ValueError("Please specify noise_model")

        # Adjust factors / coefficients to test:
        # Note that the continuous covariate does not necessarily have to be tested,
        # it could also be a condition effect or similar.
        # TODO handle interactions
        if continuous in factor_loc_totest:
            # Create reduced set of factors to test which does not contain continuous:
            factor_loc_totest_new = [x for x in factor_loc_totest if x != continuous]
            # Add spline basis terms in instead of continuous term:
            factor_loc_totest_new.extend(new_coefs)
        else:
            factor_loc_totest_new = factor_loc_totest

        logger.debug("model formulas assembled in de.test.continuos():")
        logger.debug("factor_loc_totest_new: " + ",".join(factor_loc_totest_new))
        logger.debug("formula_loc_new: " + formula_loc_new)
        logger.debug("formula_scale_new: " + formula_scale_new)

        de_test = wald(
            data=X,
            factor_loc_totest=factor_loc_totest_new,
            coef_to_test=None,
            formula_loc=formula_loc_new,
            formula_scale=formula_scale_new,
            as_numeric=as_numeric,
            init_a=init_a,
            init_b=init_b,
            gene_names=gene_names,
            sample_description=sample_description,
            noise_model=noise_model,
            size_factors=size_factors,
            batch_size=batch_size,
            training_strategy=training_strategy,
            quick_scale=quick_scale,
            dtype=dtype,
            **kwargs
        )
        de_test = DifferentialExpressionTestWaldCont(
            de_test=de_test,
            size_factors=size_factors,
            continuous_coords=sample_description[continuous].values,
            spline_coefs=new_coefs
        )
    elif test.lower() == 'lrt':
        if noise_model is None:
            raise ValueError("Please specify noise_model")
        full_formula_loc = formula_loc_new
        # Assemble reduced loc model:
        formula_scale_new = formula_scale.split(continuous)
        formula_scale_new = formula_term_continuous.join(formula_scale_new)
        reduced_formula_loc = formula_scale.split('+')
        # Take out terms in reduced location model which are to be tested:
        reduced_formula_loc = [x for x in reduced_formula_loc if x not in factor_loc_totest]
        reduced_formula_loc = '+'.join(reduced_formula_loc)
        # Replace occurences of continuous term in reduced model:
        reduced_formula_loc = reduced_formula_loc.split(continuous)
        reduced_formula_loc = formula_term_continuous.join(reduced_formula_loc)

        # Scale model is not tested:
        full_formula_scale = formula_scale_new
        reduced_formula_scale = formula_scale_new

        logger.debug("model formulas assembled in de.test.continuous():")
        logger.debug("full_formula_loc: " + full_formula_loc)
        logger.debug("reduced_formula_loc: " + reduced_formula_loc)
        logger.debug("full_formula_scale: " + full_formula_scale)
        logger.debug("reduced_formula_scale: " + reduced_formula_scale)

        de_test = lrt(
            data=X,
            full_formula_loc=full_formula_loc,
            reduced_formula_loc=reduced_formula_loc,
            full_formula_scale=full_formula_scale,
            reduced_formula_scale=reduced_formula_scale,
            as_numeric=as_numeric,
            init_a=init_a,
            init_b=init_b,
            gene_names=gene_names,
            sample_description=sample_description,
            noise_model=noise_model,
            size_factors=size_factors,
            batch_size=batch_size,
            training_strategy=training_strategy,
            quick_scale=quick_scale,
            dtype=dtype,
            **kwargs
        )
        de_test = DifferentialExpressionTestLRTCont(
            de_test=de_test,
            size_factors=size_factors,
            continuous_coords=sample_description[continuous].values,
            spline_coefs=new_coefs
        )
    else:
        raise ValueError('base.continuous(): Parameter `test` not recognized.')

    return de_test
