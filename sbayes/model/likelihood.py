#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum

from numpy.core._umath_tests import inner1d

from sbayes.sampling.counts import recalculate_feature_counts
from sbayes.util import dirichlet_categorical_logpdf, timeit, normalize, \
    gaussian_mu_marginalised_logpdf, lh_poisson_lambda_marginalised_logpdf

try:
    from typing import Protocol
except ImportError:
    from typing_extensions import Protocol

import numpy as np
from numpy.typing import NDArray

from scipy.stats import nbinom
from sbayes.sampling.state import Sample, GenericTypeSample, ModelCache, GenericTypeCache
from sbayes.load_data import Data, Confounder, Features
from sbayes.model.model_shapes import ModelShapes

class Likelihood(object):

    categorical: LikelihoodCategorical
    gaussian: LikelihoodGaussian
    poisson: LikelihoodPoisson
    logitnormal: LikelihoodLogitNormal

    def __init__(self, data: Data, shapes: ModelShapes, prior):
        self.features = data.features
        self.confounders = data.confounders
        self.shapes = shapes
        self.prior = prior
        self.source_index = {'clusters': 0}
        for i, conf in enumerate(self.confounders, start=1):
            self.source_index[conf] = i

        self.categorical = LikelihoodCategorical(features=self.features, confounders=self.confounders,
                                                 shapes=self.shapes, prior=self.prior, has_counts=True)
        self.gaussian = LikelihoodGaussian(features=self.features, confounders=self.confounders,
                                           shapes=self.shapes, prior=self.prior)
        self.poisson = LikelihoodPoisson(features=self.features, confounders=self.confounders,
                                         shapes=self.shapes, prior=self.prior)
        self.logitnormal = LikelihoodLogitNormal(features=self.features, confounders=self.confounders,
                                                 shapes=self.shapes, prior=self.prior)
        self.feature_type_likelihoods = [
            self.categorical,
            self.gaussian,
            self.poisson,
            self.logitnormal
        ]

    def __call__(self, sample: Sample, caching=True) -> float:
        """Compute the likelihood of all sites. The likelihood is defined as a mixture of areal and confounding effects.
            Args:
                sample: A Sample object consisting of clusters and weights
            Returns:
                The joint likelihood of the current sample
        """

        log_lh = 0.0

        # Sum up log-likelihood of each mixture component and each type of feature
        if sample.categorical is not None:
            log_lh += self.categorical(sample=sample, caching=caching)
        if sample.gaussian is not None:
            log_lh += self.gaussian(sample=sample, caching=caching)
        if sample.poisson is not None:
            log_lh += self.poisson(sample=sample, caching=caching)
        if sample.logitnormal is not None:
            log_lh += self.logitnormal(sample=sample, caching=caching)
        return log_lh


class LikelihoodGenericType(object):

    """Likelihood of the sBayes model.

    Attributes:
        features (np.array): The values for all sites and features.
            shape: (n_objects, n_features, n_categories)
        confounders (dict): Assignment of objects to confounders. For each confounder (c) one np.array
            with shape: (n_groups(c), n_objects)
        shapes (ModelShapes): A dataclass with shape information for building the Likelihood and Prior objects
        has_counts: Does the likelihood use counts and should they be cached/recalculated? (currently only for
            categorical features)
    """
    def __init__(self, features: Features, confounders, shapes: ModelShapes, prior, has_counts=False):
        """"""
        self.features = features
        self.confounders = confounders
        self.shapes = shapes
        self.prior = prior
        self.has_counts = has_counts

    def is_used(self, sample: Sample):
        raise NotImplementedError

    def __call__(self, sample: Sample, caching=True) -> float:
        """Compute the likelihood of all sites. The likelihood is defined as a mixture of areal and confounding effects.
            Args:
                sample: A Sample object consisting of clusters and weights
            Returns:
                The joint likelihood of the current sample
        """

        if not caching:
            recalculate_feature_counts(self.features.categorical.values, sample)

        # Sum up log-likelihood of each mixture component:
        log_lh = 0.0

        log_lh += self.compute_lh_clusters(sample, caching=caching)
        for i, conf in enumerate(self.confounders, start=1):
            log_lh += self.compute_lh_confounder(sample, conf, caching=caching)

        return log_lh

    def compute_lh_clusters(self, sample: Sample, caching=True) -> float:
        """Compute the log-likelihood of the observations that are assigned to cluster
        effects in the current sample."""
        raise NotImplementedError

    def compute_lh_confounder(self, sample: Sample, conf: str, caching=True) -> float:
        """Compute the log-likelihood of the observations that are assigned to confounder
        `conf` in the current sample."""
        raise NotImplementedError

    def get_ft_cache(self, cache: ModelCache) -> GenericTypeCache:
        raise NotImplementedError

    def pointwise_likelihood(
        self,
        model,
        sample: Sample,
        caching=True
    ) -> NDArray[float]:  # shape: (n_objects, n_feature, n_components)
        """Update the likelihood values for each of the mixture components"""
        CHECK_CACHING = False

        lh_cache = self.get_ft_cache(sample.cache).component_likelihoods


        if caching and not lh_cache.is_outdated():
            if CHECK_CACHING:
                assert np.all(lh_cache.value == self.pointwise_likelihood(model, sample, caching=False))
            return lh_cache.value

        with lh_cache.edit() as component_likelihood:
            changed_clusters = lh_cache.what_changed(input_key=['clusters', 'clusters_sufficient_stats'],
                                                     caching=caching)
            if len(changed_clusters) > 0:
                # Update component likelihood for cluster effects:
                self.compute_pointwise_cluster_likelihood(
                    sample=sample,
                    changed_clusters=changed_clusters,
                    out=component_likelihood[..., 0],
                )

            # Update component likelihood for confounding effects:
            for i, conf in enumerate(self.confounders.values(), start=1):
                changed_groups = lh_cache.what_changed(input_key=f'{conf.name}_sufficient_stats', caching=caching)
                if len(changed_groups) == 0:
                    continue

                self.compute_pointwise_confounder_likelihood(
                    confounder=conf,
                    sample=sample,
                    changed_groups=changed_groups,
                    out=component_likelihood[..., i],
                )

            component_likelihood[self.na_values()] = 1.

        if caching and CHECK_CACHING:
            cached = np.copy(lh_cache.value)
            recomputed = self.pointwise_likelihood(model, sample, caching=False)
            assert np.allclose(cached, recomputed)

        return lh_cache.value

    def compute_pointwise_cluster_likelihood(
        self,
        sample: Sample,
        changed_clusters: list[int],
        out: NDArray[float],
    ):
        raise NotImplementedError

    def compute_pointwise_confounder_likelihood(
        self,
        confounder: Confounder,
        sample: Sample,
        changed_groups: list[int],
        out: NDArray[float],
    ):
        raise NotImplementedError

    def pointwise_conditional_cluster_lh(
        self,
        sample: Sample,
        i_cluster: int,
        available: NDArray[bool]
    ) -> NDArray[float]:
        raise NotImplementedError

    def na_values(self):
        pass


class FeatureType(str, Enum):
    categorical = "categorical"
    gaussian = "gaussian"
    poisson = "poisson"
    logitnormal = "logitnormal"


class LikelihoodCategorical(LikelihoodGenericType):

    feature_type = FeatureType.categorical

    def is_used(self, sample: Sample):
        return sample.categorical is not None

    def get_ft_cache(self, cache: ModelCache):
        return cache.categorical

    def compute_lh_clusters(self, sample: Sample, caching=True) -> float:
        """Compute the log-likelihood of the observations that are assigned to cluster
        effects in the current sample."""

        cache = sample.cache.categorical.group_likelihoods['clusters']
        feature_counts = sample.categorical.sufficient_statistics['clusters'].value

        with cache.edit() as lh:
            for i_cluster in cache.what_changed('sufficient_stats', caching=caching):
                lh[i_cluster] = dirichlet_categorical_logpdf(
                    counts=feature_counts[i_cluster],
                    a=self.prior.prior_cluster_effect.categorical.concentration_array,
                ).sum()
        return cache.value.sum()

    def compute_lh_confounder(self, sample: Sample, conf: str, caching=True) -> float:
        """Compute the log-likelihood of the observations that are assigned to confounder
        `conf` in the current sample."""

        cache = sample.cache.categorical.group_likelihoods[conf]
        feature_counts = sample.categorical.sufficient_statistics[conf].value

        with cache.edit() as lh:
            prior_concentration = self.prior.prior_confounding_effects[conf].categorical.concentration_array(sample)
            for i_g in cache.what_changed('sufficient_stats', caching=caching):
                lh[i_g] = dirichlet_categorical_logpdf(
                    counts=feature_counts[i_g],
                    a=prior_concentration[i_g],
                ).sum()

        return cache.value.sum()

    def compute_pointwise_cluster_likelihood(
        self,
        sample: Sample,
        changed_clusters: list[int],
        out: NDArray[float],
    ):

        # The expected cluster effect is given by the normalized posterior counts
        cluster_effect_counts = (  # feature counts + prior counts
                sample.categorical.sufficient_statistics['clusters'].value +
                self.prior.prior_cluster_effect.categorical.concentration_array
        )

        cluster_effect = normalize(cluster_effect_counts, axis=-1)

        compute_component_likelihood(
            features=self.features.categorical.values,
            probs=cluster_effect,
            groups=sample.clusters.value,
            changed_groups=changed_clusters,
            out=out,
        )

    def compute_pointwise_confounder_likelihood(
        self,
        confounder: Confounder,
        sample: Sample,
        changed_groups: list[int],
        out: NDArray[float],
    ):
        groups = confounder.group_assignment
        # The expected confounding effect is given by the normalized posterior counts
        prior = self.prior.prior_confounding_effects[confounder.name].categorical

        conf_effect_counts = (  # feature counts + prior counts
                sample.categorical.sufficient_statistics[confounder.name].value +
                prior.concentration_array(sample)
        )
        conf_effect = normalize(conf_effect_counts, axis=-1)

        compute_component_likelihood(
            features=self.features.categorical.values,
            probs=conf_effect,
            groups=groups,
            changed_groups=changed_groups,
            out=out,
        )

    def pointwise_conditional_cluster_lh(
        self,
        sample: Sample,
        i_cluster: int,
        available: NDArray[bool]
    ) -> NDArray[float]:
        prior_counts = self.prior.prior_cluster_effect.categorical.concentration_array
        feature_counts = sample.categorical.sufficient_statistics['clusters'].value[[i_cluster]]

        p = normalize(prior_counts + feature_counts, axis=-1)
        return inner1d(self.features.categorical.values[available], p)

    def na_values(self):
        return self.features.categorical.na_values


class LikelihoodGaussian(LikelihoodGenericType):

    feature_type = FeatureType.gaussian

    def is_used(self, sample: Sample):
        return sample.gaussian is not None

    def get_ft_cache(self, cache: ModelCache):
        return cache.gaussian

    def compute_lh_clusters(self, sample: Sample, caching=True) -> float:
        """Compute the log-likelihood of the observations that are assigned to cluster
        effects in the current sample."""

        cache = sample.cache.gaussian.group_likelihoods['clusters']

        with cache.edit() as lh:
            for i_cluster in cache.what_changed('sufficient_stats', caching=caching):
                lh[i_cluster] = 0
                cluster = sample.clusters.value[i_cluster]

                # todo: Consider vectorization
                for f in range(self.features.gaussian.n_features):
                    # TODO only use samples that have cluster as a source component
                    x = self.features.gaussian.values[cluster][:, f]
                    mu_0 = self.prior.prior_cluster_effect.gaussian.mean.mu_0_array[f]
                    sigma_0 = self.prior.prior_cluster_effect.gaussian.mean.sigma_0_array[f]
                    # use the maximum likelihood value for sigma_fixed
                    sigma_fixed = np.nanstd(x)
                    lh[i_cluster] += gaussian_mu_marginalised_logpdf(
                        x=x, mu_0=mu_0, sigma_0=sigma_0, sigma_fixed=sigma_fixed,
                        in_component=sample.gaussian.source.value[cluster, f, 0],
                    )

        return cache.value.sum()

    def compute_lh_confounder(self, sample: Sample, conf: str, caching=True) -> float:
        """Compute the log-likelihood of the observations that are assigned to confounder
        `conf` in the current sample."""

        cache = sample.cache.gaussian.group_likelihoods[conf]
        i_component = sample.model_shapes.get_component_index(conf)

        with cache.edit() as lh:
            for i_g in cache.what_changed('sufficient_stats', caching=caching):
                lh[i_g] = 0
                group = sample.confounders[conf].group_assignment[i_g]

                # todo: Consider vectorization
                for f in range(self.features.gaussian.n_features):
                    x = self.features.gaussian.values[group, f]
                    mu_0 = self.prior.prior_confounding_effects[conf].gaussian.mean.mu_0_array[i_g][f]
                    sigma_0 = self.prior.prior_confounding_effects[conf].gaussian.mean.sigma_0_array[i_g][f]

                    # use the maximum likelihood value for sigma_fixed
                    sigma_fixed = np.nanstd(x)
                    lh[i_g] += gaussian_mu_marginalised_logpdf(
                        x=x, mu_0=mu_0, sigma_0=sigma_0, sigma_fixed=sigma_fixed,
                        in_component=sample.gaussian.source.value[group, f, i_component],
                    )

        return cache.value.sum()

    def compute_pointwise_cluster_likelihood(
        self,
        sample: Sample,
        changed_clusters: list[int],
        out: NDArray[float],
    ):

        mu_0 = self.prior.prior_cluster_effect.gaussian.mean.mu_0_array
        sigma_0 = self.prior.prior_cluster_effect.gaussian.mean.sigma_0_array
        # Jeffreys prior for variance

        groups = sample.clusters.value
        features = self.features.gaussian.values

        out[~groups.any(axis=0), :] = 0.

        for i in changed_clusters:
            g = groups[i]
            f_g = features[g, :]

            for j in range(f_g.shape[1]):
                f = f_g[:, j]
                # todo: implement likelihood
                out[g, j] = np.repeat(0.5, len(f))

    def compute_pointwise_confounder_likelihood(
            self,
            confounder: Confounder,
            sample: Sample,
            changed_groups: list[int],
            out: NDArray[float],
    ):

        mu_0 = self.prior.prior_confounding_effects[confounder.name].gaussian.mean.mu_0_array
        sigma_0 = self.prior.prior_confounding_effects[confounder.name].gaussian.mean.sigma_0_array

        groups = confounder.group_assignment
        features = self.features.gaussian.values

        out[~groups.any(axis=0), :] = 0.

        for i in changed_groups:
            g = groups[i]
            f_g = features[g, :]

            for j in range(f_g.shape[1]):
                f = f_g[:, j]
                # todo: implement likelihood
                out[g, j] = np.repeat(0.5, len(f))

    def pointwise_conditional_cluster_lh(
        self,
        sample: Sample,
        i_cluster: int,
        available: NDArray[bool]
    ) -> NDArray[float]:

        mu_0 = self.prior.prior_cluster_effect.gaussian.mean.mu_0_array
        sigma_0 = self.prior.prior_cluster_effect.gaussian.mean.sigma_0_array

        features_candidates = self.features.gaussian.values[available]
        features_cluster = self.features.gaussian.values[sample.clusters.value[i_cluster]]
        condition_lh = np.zeros(features_candidates.shape)

        # todo: implement likelihood
        for j in range(features_cluster.shape[1]):
            f_c = features_cluster[:, j]
            condition_lh[:, j] = np.repeat(0.5, len(features_candidates[:, j]))

        return condition_lh


class LikelihoodPoisson(LikelihoodGenericType):

    feature_type = FeatureType.poisson

    def is_used(self, sample: Sample):
        return sample.poisson is not None

    def get_ft_cache(self, cache: ModelCache):
        return cache.poisson

    def compute_lh_clusters(self, sample: Sample, caching=True) -> float:
        """Compute the log-likelihood of the observations that are assigned to cluster
        effects in the current sample."""

        cache = sample.cache.poisson.group_likelihoods['clusters']

        with cache.edit() as lh:

            for i_cluster in cache.what_changed('sufficient_stats', caching=caching):
                lh[i_cluster] = 0
                cluster = sample.clusters.value[i_cluster]

                # todo: Consider vectorization
                for f in range(self.features.poisson.n_features):
                    # TODO only use samples that have cluster as a source component
                    x = self.features.poisson.values[cluster, f]
                    alpha_0 = self.prior.prior_cluster_effect.poisson.alpha_0_array[f]
                    beta_0 = self.prior.prior_cluster_effect.poisson.beta_0_array[f]
                    lh[i_cluster] += lh_poisson_lambda_marginalised_logpdf(
                        x=x, alpha_0=alpha_0, beta_0=beta_0,
                        in_component=sample.poisson.source.value[cluster, f, 0],
                    )

        return cache.value.sum()

    def compute_lh_confounder(self, sample: Sample, conf: str, caching=True) -> float:
        """Compute the log-likelihood of the observations that are assigned to confounder
        `conf` in the current sample."""

        cache = sample.cache.poisson.group_likelihoods[conf]
        i_component = sample.model_shapes.get_component_index(conf)

        with cache.edit() as lh:
            for i_g in cache.what_changed('sufficient_stats', caching=caching):
                lh[i_g] = 0
                group = sample.confounders[conf].group_assignment[i_g]

                # todo: Consider vectorization
                for f in range(self.features.poisson.n_features):
                    x = self.features.poisson.values[group, f]
                    alpha_0 = self.prior.prior_confounding_effects[conf].poisson.alpha_0_array[i_g][f]
                    beta_0 = self.prior.prior_confounding_effects[conf].poisson.beta_0_array[i_g][f]

                    lh[i_g] += lh_poisson_lambda_marginalised_logpdf(
                        x=x, alpha_0=alpha_0, beta_0=beta_0,
                        in_component=sample.poisson.source.value[group, f, i_component],
                    )

        return cache.value.sum()

    def compute_pointwise_cluster_likelihood(
            self,
            sample: Sample,
            changed_clusters: list[int],
            out: NDArray[float],
    ):
        alpha_0 = self.prior.prior_cluster_effect.poisson.alpha_0_array
        beta_0 = self.prior.prior_cluster_effect.poisson.beta_0_array
        groups = sample.clusters.value
        features = self.features.poisson.values

        out[~groups.any(axis=0), :] = 0.

        for i in changed_clusters:
            g = groups[i]
            f_g = features[g, :]

            for j in range(f_g.shape[1]):

                f = f_g[:, j]
                n = len(f) - 1
                alpha_1 = alpha_0[j] + f.sum() - f
                beta_1 = beta_0[j] + n
                out[g, j] = nbinom.pmf(f, alpha_1, beta_1 / (1 + beta_1))

    def compute_pointwise_confounder_likelihood(
            self,
            confounder: Confounder,
            sample: Sample,
            changed_groups: list[int],
            out: NDArray[float],
    ):

        alpha_0 = self.prior.prior_confounding_effects[confounder.name].poisson.alpha_0_array
        beta_0 = self.prior.prior_confounding_effects[confounder.name].poisson.beta_0_array
        groups = confounder.group_assignment
        features = self.features.poisson.values

        out[~groups.any(axis=0), :] = 0.

        for i in changed_groups:
            g = groups[i]
            f_g = features[g, :]

            for j in range(f_g.shape[1]):
                f = f_g[:, j]
                n = len(f) - 1
                alpha_1 = alpha_0[i, j] + f.sum() - f
                beta_1 = beta_0[i, j] + n

                out[g, j] = nbinom.pmf(f, alpha_1, beta_1 / (1 + beta_1))

    def pointwise_conditional_cluster_lh(
        self,
        sample: Sample,
        i_cluster: int,
        available: NDArray[bool]
    ) -> NDArray[float]:
        alpha_0 = self.prior.prior_cluster_effect.poisson.alpha_0_array
        beta_0 = self.prior.prior_cluster_effect.poisson.beta_0_array

        features_candidates = self.features.poisson.values[available]
        features_cluster = self.features.poisson.values[sample.clusters.value[i_cluster]]
        condition_lh = np.zeros(features_candidates.shape)

        for j in range(features_cluster.shape[1]):
            f_c = features_cluster[:, j]
            n = len(f_c)
            alpha_1 = alpha_0[j] + f_c.sum()
            beta_1 = beta_0[j] + n
            condition_lh[:, j] = nbinom.pmf(features_candidates[:, j], alpha_1, beta_1 / (1 + beta_1))

        return condition_lh


class LikelihoodLogitNormal(LikelihoodGenericType):

    feature_type = FeatureType.logitnormal

    def is_used(self, sample: Sample):
        return sample.logitnormal is not None

    def get_ft_cache(self, cache: ModelCache):
        return cache.logitnormal

    def compute_lh_clusters(self, sample: Sample, caching=True) -> float:
        """Compute the log-likelihood of the observations that are assigned to cluster
        effects in the current sample."""

        cache = sample.cache.logitnormal.group_likelihoods['clusters']

        with cache.edit() as lh:
            for i_cluster in cache.what_changed('sufficient_stats', caching=caching):
                lh[i_cluster] = 0
                cluster = sample.clusters.value[i_cluster]

                # todo: Consider vectorization
                for f in range(self.features.logitnormal.n_features):
                    # TODO only use samples that have cluster as a source component
                    x = self.features.logitnormal.values[cluster, f]
                    mu_0 = self.prior.prior_cluster_effect.logitnormal.mean.mu_0_array[f]
                    sigma_0 = self.prior.prior_cluster_effect.logitnormal.mean.sigma_0_array[f]
                    # use the maximum likelihood value for sigma_fixed
                    sigma_fixed = np.nanstd(x)
                    lh[i_cluster] += gaussian_mu_marginalised_logpdf(
                        x=x, mu_0=mu_0, sigma_0=sigma_0, sigma_fixed=sigma_fixed,
                        in_component=sample.logitnormal.source.value[cluster, f, 0],
                    )

        return cache.value.sum()

    def compute_lh_confounder(self, sample: Sample, conf: str, caching=True) -> float:
        """Compute the log-likelihood of the observations that are assigned to confounder
        `conf` in the current sample."""

        cache = sample.cache.logitnormal.group_likelihoods[conf]
        i_component = sample.model_shapes.get_component_index(conf)

        with cache.edit() as lh:
            for i_g in cache.what_changed('sufficient_stats', caching=caching):
                lh[i_g] = 0
                group = sample.confounders[conf].group_assignment[i_g]

                # todo: Consider vectorization
                for f in range(self.features.logitnormal.n_features):
                    x = self.features.logitnormal.values[group, f]
                    mu_0 = self.prior.prior_confounding_effects[conf].logitnormal.mean.mu_0_array[i_g][f]
                    sigma_0 = self.prior.prior_confounding_effects[conf].logitnormal.mean.sigma_0_array[i_g][f]

                    # use the maximum likelihood value for sigma_fixed
                    sigma_fixed = np.nanstd(x)
                    lh[i_g] += gaussian_mu_marginalised_logpdf(
                        x=x, mu_0=mu_0, sigma_0=sigma_0, sigma_fixed=sigma_fixed,
                        in_component=sample.logitnormal.source.value[group, f, i_component],
                    )

        return cache.value.sum()

    def compute_pointwise_cluster_likelihood(
        self,
        sample: Sample,
        changed_clusters: list[int],
        out: NDArray[float],
    ):

        mu_0 = self.prior.prior_cluster_effect.logitnormal.mean.mu_0_array
        sigma_0 = self.prior.prior_cluster_effect.logitnormal.mean.sigma_0_array
        # Jeffreys prior for variance

        groups = sample.clusters.value
        features = self.features.logitnormal.values

        out[~groups.any(axis=0), :] = 0.

        for i in changed_clusters:
            g = groups[i]
            f_g = features[g, :]

            for j in range(f_g.shape[1]):
                f = f_g[:, j]
                # todo: implement likelihood
                out[g, j] = np.repeat(0.5, len(f))

    def compute_pointwise_confounder_likelihood(
            self,
            confounder: Confounder,
            sample: Sample,
            changed_groups: list[int],
            out: NDArray[float],
    ):

        mu_0 = self.prior.prior_confounding_effects[confounder.name].logitnormal.mean.mu_0_array
        sigma_0 = self.prior.prior_confounding_effects[confounder.name].logitnormal.mean.sigma_0_array

        groups = confounder.group_assignment
        features = self.features.logitnormal.values

        out[~groups.any(axis=0), :] = 0.

        for i in changed_groups:
            g = groups[i]
            f_g = features[g, :]

            for j in range(f_g.shape[1]):
                f = f_g[:, j]
                # todo: implement likelihood
                out[g, j] = np.repeat(0.5, len(f))

    def pointwise_conditional_cluster_lh(
        self,
        sample: Sample,
        i_cluster: int,
        available: NDArray[bool]
    ) -> NDArray[float]:

        mu_0 = self.prior.prior_cluster_effect.logitnormal.mean.mu_0_array
        sigma_0 = self.prior.prior_cluster_effect.logitnormal.mean.sigma_0_array

        features_candidates = self.features.logitnormal.values[available]
        features_cluster = self.features.logitnormal.values[sample.clusters.value[i_cluster]]
        condition_lh = np.zeros(features_candidates.shape)

        # todo: implement likelihood
        for j in range(features_cluster.shape[1]):
            f_c = features_cluster[:, j]
            condition_lh[:, j] = np.repeat(0.5, len(features_candidates[:, j]))

        return condition_lh

def compute_component_likelihood(
    features: NDArray[bool],    # shape: (n_objects, n_features, n_states)
    probs: NDArray[float],      # shape: (n_groups, n_features, n_states)
    groups: NDArray[bool],      # shape: (n_groups, n_objects)
    changed_groups: list[int],
    out: NDArray[float]         # shape: (n_objects, n_features)
) -> NDArray[float]:            # shape: (n_objects, n_features)

    # [NN] Idea: If the majority of groups is outdated, a full update (w/o caching) may be faster
    # [NN] ...does not seem like it -> deactivate for now.
    # if len(changed_groups) > 0.8 * groups.shape[0]:
    #     return np.einsum('ijk,hjk,hi->ij', features, probs, groups, optimize=True)

    out[~groups.any(axis=0), :] = 0.
    for i in changed_groups:
        g = groups[i]
        f_g = features[g, :, :]
        p_g = probs[i, :, :]
        out[g, :] = np.einsum('ijk,jk->ij', f_g, p_g)
        assert np.allclose(out[g, :], np.sum(f_g * p_g[np.newaxis, ...], axis=-1))
        assert np.allclose(out[g, :], inner1d(f_g, p_g[np.newaxis, ...]))

    return out


def update_categorical_weights(sample: Sample, caching=True) -> NDArray[float]:
    """Compute the normalized weights of each component at each site for categorical features
    Args:
        sample: the current MCMC sample.
        caching: ignore cache if set to false.
    Returns:
        np.array: normalized weights of each component at each site for categorical features
            shape: (n_objects, n_categorical_features, 1 + n_confounders)
    """

    cache = sample.cache.categorical.weights_normalized

    if (not caching) or cache.is_outdated():
        w_normed = normalize_weights(sample.categorical.weights.value, sample.cache.categorical.has_components.value)
        cache.update_value(w_normed)

    return cache.value


def update_gaussian_weights(sample: Sample, caching=True) -> NDArray[float]:
    """Compute the normalized weights of each component at each site for gaussian features
    Args:
        sample: the current MCMC sample.
        caching: ignore cache if set to false.
    Returns:
        np.array: normalized weights of each component at each site for gaussian features
            shape: (n_objects, n_gaussian_features, 1 + n_confounders)
    """

    cache = sample.cache.gaussian.weights_normalized

    if (not caching) or cache.is_outdated():
        w_normed = normalize_weights(sample.gaussian.weights.value, sample.cache.gaussian.has_components.value)
        cache.update_value(w_normed)

    return cache.value


def update_poisson_weights(sample: Sample, caching=True) -> NDArray[float]:
    """Compute the normalized weights of each component at each site for poisson features
    Args:
        sample: the current MCMC sample.
        caching: ignore cache if set to false.
    Returns:
        np.array: normalized weights of each component at each site for poisson features
            shape: (n_objects, n_poisson_features, 1 + n_confounders)
    """

    cache = sample.cache.poisson.weights_normalized

    if (not caching) or cache.is_outdated():
        w_normed = normalize_weights(sample.poisson.weights.value, sample.cache.poisson.has_components.value)
        cache.update_value(w_normed)

    return cache.value


def update_logitnormal_weights(sample: Sample, caching=True) -> NDArray[float]:
    """Compute the normalized weights of each component at each site for logitnormal features
    Args:
        sample: the current MCMC sample.
        caching: ignore cache if set to false.
    Returns:
        np.array: normalized weights of each component at each site for logitnormal features
            shape: (n_objects, n_logitnormal_features, 1 + n_confounders)
    """

    cache = sample.cache.logitnormal.weights_normalized

    if (not caching) or cache.is_outdated():
        w_normed = normalize_weights(sample.logitnormal.weights.value, sample.cache.logitnormal.has_components.value)
        cache.update_value(w_normed)

    return cache.value


def normalize_weights(
    weights: NDArray[float],  # shape: (n_features, 1 + n_confounders)
    has_components: NDArray[bool]  # shape: (n_objects, 1 + n_confounders)
) -> NDArray[float]:  # shape: (n_objects, n_features, 1 + n_confounders)
    """This function assigns each site a weight if it has a likelihood and zero otherwise
    Args:
        weights: the weights to normalize
        has_components: indicators for which objects are affected by cluster and confounding effects
    Return:
        the weight_per site
    """
    # Find the unique patterns in `has_components` and remember the inverse mapping
    pattern, pattern_inv = np.unique(has_components, axis=0, return_inverse=True)

    # Calculate the normalized weights per pattern
    w_per_pattern = pattern[:, None, :] * weights[None, :, :]
    w_per_pattern /= np.sum(w_per_pattern, axis=-1, keepdims=True)

    # Broadcast the normalized weights per pattern to the objects where the patterns appeared using pattern_inv
    return w_per_pattern[pattern_inv]

    # # Broadcast weights to each site and mask with the has_components arrays
    # # Broadcasting:
    # #   `weights` does not know about sites -> add axis to broadcast to the sites-dimension of `has_component`
    # #   `has_components` does not know about features -> add axis to broadcast to the features-dimension of `weights`
    # weights_per_site = weights[np.newaxis, :, :] * has_components[:, np.newaxis, :]
    #
    # # Re-normalize the weights, where weights were masked
    # return weights_per_site / weights_per_site.sum(axis=2, keepdims=True)
