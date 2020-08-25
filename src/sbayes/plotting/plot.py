""" Class Plot

Defines general functions which are used in the child classes Trace and Map
Manages loading of input data, config files and the general graphic parameters of the plots
"""

import csv
import json
import os
from statistics import median

import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
import numpy as np
import seaborn as sns
import math

from sbayes.preprocessing import compute_network, read_sites
from sbayes.util import parse_area_columns, read_features_from_csv


class Plot:
    def __init__(self, simulated_data=False):

        # Flag for simulation
        self.is_simulation = simulated_data

        # Config variables
        self.config = {}
        self.config_file = None

        # Path variables
        self.path_results = None
        self.path_data = None
        self.path_plots = None

        self.path_areas = None
        self.path_stats = None

        if self.is_simulation:
            self.path_ground_truth_areas = None
            self.path_ground_truth_stats = None

        # Input areas and stats
        self.areas = []
        self.stats = []

        self.number_features = 0

        # Input sites, site_names, network, ...
        self.sites = None
        self.site_names = None
        self.network = None
        self.locations = None
        self.dist_mat = None

        # Input ground truth areas and stats (for simulation)
        if self.is_simulation:
            self.areas_ground_truth = []

        # Dictionary with all the MCMC results
        self.results = {}

        # Needed for the weights and parameters plotting
        plt.style.use('seaborn-paper')
        plt.tight_layout()

    ####################################
    # Configure the parameters
    ####################################
    def load_config(self, config_file):

        # Get parameters from config_file
        self.config_file = config_file

        # Read config file
        self.read_config()

        # Convert lists to tuples
        self.convert_config(self.config)

        # Verify config
        self.verify_config()

        # Assign global variables for more convenient workflow
        self.path_results = self.config['input']['path_results']
        self.path_data = self.config['input']['path_data']
        self.path_plots = self.config['input']['path_results'] + '/plots'

        self.path_areas = self.config['input']['path_areas']
        self.path_stats = self.config['input']['path_stats']

        if self.is_simulation:
            self.path_ground_truth_areas = self.config['input']['path_ground_truth_areas']
            self.path_ground_truth_stats = self.config['input']['path_ground_truth_stats']

        if not os.path.exists(self.path_plots):
            os.makedirs(self.path_plots)

    def read_config(self):
        with open(self.config_file, 'r') as f:
            self.config = json.load(f)

    @staticmethod
    def convert_config(d):
        for k, v in d.items():
            if isinstance(v, dict):
                Plot.convert_config(v)
            else:
                if k != 'scenarios' and k != 'post_freq_lines' and type(v) == list:
                    d[k] = tuple(v)

    def verify_config(self):
        pass

    ####################################
    # Read the data and the results
    ####################################

    # Functions related to the current scenario (run in a loop over scenarios, i.e. n_zones)
    # Set the results path for the current scenario
    def set_scenario_path(self, current_scenario):
        current_run_path = f"{self.path_plots}/n{self.config['input']['run']}_{current_scenario}/"

        if not os.path.exists(current_run_path):
            os.makedirs(current_run_path)

        return current_run_path

    # Read sites, site_names, network
    def read_data(self):

        if self.is_simulation:
            self.sites, self.site_names, _ = read_sites(self.path_data)
        else:
            self.sites, self.site_names, _, _ , _, _, _, _ = read_features_from_csv(self.path_data)

        self.network = compute_network(self.sites)
        self.locations, self.dist_mat = self.network['locations'], self.network['dist_mat']

    # Read areas
    # Read the data from the files:
    # ground_truth/areas.txt
    # <experiment_path>/areas_<scenario>.txt
    @staticmethod
    def read_areas(txt_path):
        result = []

        with open(txt_path, 'r') as f_sample:

            # This makes len(result) = number of areas (flipped array)

            # Split the sample
            # len(byte_results) equals the number of samples
            byte_results = (f_sample.read()).split('\n')

            # Get the number of areas
            n_areas = len(byte_results[0].split('\t'))

            # Append empty arrays to result, so that len(result) = n_areas
            for i in range(n_areas):
                result.append([])

            # Process each sample
            for sample in byte_results:

                # Exclude empty lines
                if len(sample) > 0:

                    # Parse each sample
                    # len(parsed_result) equals the number of areas
                    # parse_area_columns.shape equals (n_areas, n_sites)
                    parsed_sample = parse_area_columns(sample)

                    # Add each item in parsed_area_columns to the corresponding array in result
                    for j in range(len(parsed_sample)):

                        # For ground truth
                        if len(parsed_sample) == 1:
                            result[j] = parsed_sample[j]

                        # For all samples
                        else:
                            result[j].append(parsed_sample[j])

        return result

    # Helper function for read_stats
    # Used for reading: weights, alpha, beta, gamma
    @staticmethod
    def read_dictionary(txt_path, lines, current_key, search_key, param_dict):
        if 'ground_truth' in txt_path:
            if current_key.startswith(search_key):
                param_dict[current_key] = lines[current_key]
        else:
            if current_key.startswith(search_key):
                if current_key in param_dict:
                    param_dict[current_key].append(lines[current_key])
                else:
                    param_dict[current_key] = []
        return param_dict

    # Helper function for read_stats
    # Used for reading: true_posterior, true_likelihood, true_prior,
    # true_weights, true_alpha, true_beta, true_gamma,
    # recall, precision
    @staticmethod
    def read_simulation_stats(txt_path, lines):
        recall, precision, true_families = [], [], []
        true_weights, true_alpha, true_beta, true_gamma = {}, {}, {}, {}
        true_posterior, true_likelihood, true_prior = 0, 0, 0

        if 'ground_truth' in txt_path:
            true_posterior = lines['posterior']
            true_likelihood = lines['likelihood']
            true_prior = lines['prior']

            for key in lines:
                true_weights = Plot.read_dictionary(txt_path, lines, key, 'w_', true_weights)
                true_alpha = Plot.read_dictionary(txt_path, lines, key, 'alpha_', true_alpha)
                true_beta = Plot.read_dictionary(txt_path, lines, key, 'beta_', true_beta)
                true_gamma = Plot.read_dictionary(txt_path, lines, key, 'gamma_', true_gamma)

        else:
            recall.append(lines['recall'])
            precision.append(lines['precision'])

        return recall, precision, \
            true_posterior, true_likelihood, true_prior, \
            true_weights, true_alpha, true_beta, true_gamma


    # Helper function for read_stats
    # Bind all statistics together into the dictionary self.results
    def bind_stats(self, txt_path, posterior, likelihood, prior,
                   weights, alpha, beta, gamma,
                   posterior_single_zones, likelihood_single_zones, prior_single_zones,
                   recall, precision,
                   true_posterior, true_likelihood, true_prior,
                   true_weights, true_alpha, true_beta, true_gamma, feature_names):

        if 'ground_truth' in txt_path:
            self.results['true_posterior'] = true_posterior
            self.results['true_likelihood'] = true_likelihood
            self.results['true_prior'] = true_prior
            self.results['true_weights'] = true_weights
            self.results['true_alpha'] = true_alpha
            self.results['true_beta'] = true_beta
            self.results['true_gamma'] = true_gamma

        else:
            self.results['posterior'] = posterior
            self.results['likelihood'] = likelihood
            self.results['prior'] = prior
            self.results['weights'] = weights
            self.results['alpha'] = alpha
            self.results['beta'] = beta
            self.results['gamma'] = gamma
            self.results['posterior_single_zones'] = posterior_single_zones
            self.results['likelihood_single_zones'] = likelihood_single_zones
            self.results['prior_single_zones'] = prior_single_zones
            self.results['recall'] = recall
            self.results['precision'] = precision
            self.results['feature_names'] = feature_names

    # Read stats
    # Read the results from the files:
    # ground_truth/stats.txt
    # <experiment_path>/stats_<scenario>.txt
    def read_stats(self, txt_path, simulation_flag):
        posterior, likelihood, prior = [], [], []
        weights, alpha, beta, gamma, posterior_single_zones, likelihood_single_zones, prior_single_zones =\
            {}, {}, {}, {}, {}, {}, {}
        recall, precision, true_posterior, true_likelihood, true_prior, true_weights, \
            true_alpha, true_beta, true_gamma = None, None, None, None, None, None, None, None, None

        with open(txt_path, 'r') as f_stats:
            csv_reader = csv.DictReader(f_stats, delimiter='\t')
            for lines in csv_reader:
                posterior.append(lines['posterior'])
                likelihood.append(lines['likelihood'])
                prior.append(lines['prior'])

                for key in lines:
                    weights = Plot.read_dictionary(txt_path, lines, key, 'w_', weights)
                    alpha = Plot.read_dictionary(txt_path, lines, key, 'alpha_', alpha)
                    beta = Plot.read_dictionary(txt_path, lines, key, 'beta_', beta)
                    gamma = Plot.read_dictionary(txt_path, lines, key, 'gamma_', gamma)
                    posterior_single_zones = Plot.read_dictionary(txt_path, lines, key, 'post_', posterior_single_zones)
                    likelihood_single_zones = Plot.read_dictionary(txt_path, lines, key, 'lh_', likelihood_single_zones)
                    prior_single_zones = Plot.read_dictionary(txt_path, lines, key, 'prior_', prior_single_zones)

                if simulation_flag:
                    recall, precision, true_posterior, true_likelihood, true_prior, \
                        true_weights, true_alpha, true_beta, true_gamma = Plot.read_simulation_stats(txt_path, lines)

        # Names of distinct features
        feature_names = []
        for key in weights:
            if 'universal' in key:
                feature_names.append(str(key).rsplit('_', 1)[1])

        self.bind_stats(txt_path, posterior, likelihood, prior, weights, alpha, beta, gamma, posterior_single_zones,
                        likelihood_single_zones, prior_single_zones, recall, precision, true_posterior,
                        true_likelihood, true_prior, true_weights, true_alpha, true_beta, true_gamma, feature_names)

    # Read results
    # Call all the previous functions
    # Bind the results together into the results dictionary
    def read_results(self, current_scenario):

        # Read areas
        # areas_path = f"{self.path_results}/n{self.config['input']['run']}/" \
        #              f"areas_n{self.config['input']['run']}_{current_scenario}.txt"
        self.areas = self.read_areas(self.path_areas)
        self.results['zones'] = self.areas

        # Read stats
        # stats_path = f"{self.path_results}/n{self.config['input']['run']}/" \
        #             f"stats_n{self.config['input']['run']}_{current_scenario}.txt"
        # stats_path = self.path_results + '/n4/stats_n1_0.txt'
        self.read_stats(self.path_stats, self.is_simulation)
        # Read ground truth files
        if self.is_simulation:
            # areas_ground_truth_path = f"{self.path_results}/n{self.config['input']['run']}/ground_truth/areas.txt"
            self.areas_ground_truth = self.read_areas(self.path_ground_truth_areas)
            self.results['true_zones'] = self.areas_ground_truth

            # stats_ground_truth_path = f"{self.path_results}/n{self.config['input']['run']}/ground_truth/stats.txt"
            self.read_stats(self.path_ground_truth_stats, self.is_simulation)

    ####################################
    # Probability simplex, grid plot
    ####################################
    @staticmethod
    def get_corner_points(n, offset=0.5 * np.pi):
        """Generate corner points of a equal sided ´n-eck´."""
        angles = np.linspace(0, 2 * np.pi, n, endpoint=False) + offset
        return np.array([np.cos(angles), np.sin(angles)]).T

    @staticmethod
    def fill_outside(polygon, color, ax=None):
        """Fill the area outside the given polygon with ´color´.

        Args:
            polygon (np.array): The polygon corners in a numpy array.
                shape: (n_corners, 2)
            color (str or tuple): The fill color.
        """
        if ax is None:
            ax = plt.gca()

        n_corners = polygon.shape[0]
        i_left = np.argmin(polygon[:, 0])
        i_right = np.argmax(polygon[:, 0])

        # Find corners of bottom face
        i = i_left
        bot_x = [polygon[i, 0]]
        bot_y = [polygon[i, 1]]
        while i % n_corners != i_right:
            i += 1
            bot_x.append(polygon[i, 0])
            bot_y.append(polygon[i, 1])

        # Find corners of top face
        i = i_left
        top_x = [polygon[i, 0]]
        top_y = [polygon[i, 1]]
        while i % n_corners != i_right:
            i -= 1
            top_x.append(polygon[i, 0])
            top_y.append(polygon[i, 1])

        ymin, ymax = ax.get_ylim()
        plt.fill_between(bot_x, ymin, bot_y, color=color)
        plt.fill_between(top_x, ymax, top_y, color=color)

    # Transform weights into needed format
    def transform_weights(self, feature, b_in):

        universal_array = []
        contact_array = []
        inheritance_array = []

        sample_dict = self.results['weights']

        for key in sample_dict:
            if 'universal' in key and str(feature) in key:
                universal_array = sample_dict[key][b_in:]
            elif 'contact' in key and str(feature) in key:
                contact_array = sample_dict[key][b_in:]
            elif 'inheritance' in key and str(feature) in key:
                inheritance_array = sample_dict[key][b_in:]

        if self.is_simulation:

            true_universal = []
            true_contact = []
            true_inheritance = []

            true_dict = self.results['true_weights']

            for key in true_dict:
                if 'universal' in key and str(feature) in key:
                     true_universal = true_dict[key]
                elif 'contact' in key and str(feature) in key:
                    true_contact = true_dict[key]
                elif 'inheritance' in key and str(feature) in key:
                    true_inheritance = true_dict[key]

            ground_truth = np.array([true_universal, true_contact, true_inheritance]).astype(np.float)
        else:
            ground_truth = None
        sample = np.column_stack([universal_array, contact_array, inheritance_array]).astype(np.float)

        return sample,  ground_truth

    def transform_probability_vectors(self, feature, parameter, b_in):

        if "alpha" in parameter:
            sample_dict = self.results['alpha']
        elif "beta" in parameter:
            sample_dict = self.results['beta']
        elif "gamma" in parameter:
            sample_dict = self.results['gamma']
        else:
            raise ValueError("parameter must be alpha, beta or gamma")

        p_dict = {}
        states = []

        for key in sample_dict:
            if str(feature + '_') in key and parameter in key:
                state = str(key).rsplit('_', 1)[1]
                p_dict[state] = sample_dict[key][b_in:]
                states.append(state)

        sample = np.column_stack([p_dict[s] for s in p_dict]).astype(np.float)

        if self.is_simulation:
            if "alpha" in parameter:
                true_dict = self.results['true_alpha']
            elif "beta" in parameter:
                true_dict = self.results['true_beta']
            elif "gamma" in parameter:
                true_dict = self.results['true_gamma']
            else:
                raise ValueError("parameter must alpha, beta or gamma")
            if sample_dict.keys() != true_dict.keys():
                raise KeyError("dict keys do not match")

            true_prob = []

            for key in true_dict:
                if str(feature + '_') in key and parameter in key:
                    true_prob.append(true_dict[key])
        else:
            true_prob = None
        return sample, np.array(true_prob).astype(np.float), states

# Sort weights by median contact
    def get_parameters(self, b_in, parameter="weights"):

        par = {}
        true_par = {}
        states = {}

        for i in self.results['feature_names']:
            if parameter == "weights":
                p, true_p = self.transform_weights(feature=i, b_in=b_in)
                par[i] = p
                true_par[i] = true_p

            elif "alpha" in parameter or "beta" in parameter or "gamma" in parameter:
                p, true_p, state = self.transform_probability_vectors(feature=i, parameter=parameter, b_in=b_in)

                par[i] = p
                true_par[i] = true_p
                states[i] = state

        return par, true_par, states

    def sort_by_weights(self, w):
        sort_by = {}
        for i in self.results['feature_names']:
            sort_by[i] = median(w[i][:, 1])
        ordering = sorted(sort_by, key=sort_by.get, reverse=True)
        return ordering

    # Probability simplex (for one feature)
    def plot_weights(self, samples, feature, true_weights=None, labels=None, ax=None):
        """Plot a set of weight vectors in a 2D representation of the probability simplex.

        Args:
            samples (np.array): Sampled weight vectors to plot.
            true_weights (np.array): true weight vectors (only for simulated data)
            labels (list[str]): Labels for each weight dimension.
            ax (plt.Axis): The pyplot axis.
        """

        if ax is None:
            ax = plt.gca()
        n_samples, n_weights = samples.shape

        # Compute corners
        corners = Plot.get_corner_points(n_weights)
        # Bounding box
        xmin, ymin = np.min(corners, axis=0)
        xmax, ymax = np.max(corners, axis=0)

        # Project the samples
        samples_projected = samples.dot(corners)

        # color map
        cmap = sns.cubehelix_palette(light=1,  start=.5, rot=-.75, as_cmap=True)

        # Density and scatter plot
        plt.title(str(feature), loc='center', fontdict={'fontweight': 'bold', 'fontsize': 20})
        x = samples_projected.T[0]
        y = samples_projected.T[1]
        sns.kdeplot(x, y, shade=True, shade_lowest=True, cut=30, n_levels=100,
                    clip=([xmin, xmax], [ymin, ymax]), cmap=cmap)
        plt.scatter(x, y, color='k', lw=0, s=1, alpha=0.2)

        # Draw simplex and crop outside
        plt.fill(*corners.T, edgecolor='k', fill=False)
        Plot.fill_outside(corners, color='w', ax=ax)

        if true_weights is not None:
            true_projected = true_weights.dot(corners)
            plt.scatter(*true_projected.T, color="#ed1696", lw=0, s=100, marker="*")

        if labels is not None:
            for xy, label in zip(corners, labels):
                xy *= 1.08  # Stretch, s.t. labels don't overlap with corners
                plt.text(*xy, label, ha='center', va='center', fontdict={'fontsize': 16})

        plt.axis('equal')
        plt.axis('off')
        plt.tight_layout(0)
        plt.plot()

    def plot_probability_vectors(self, samples, feature, true_p=None, labels=None, ax=None):
        """Plot a set of weight vectors in a 2D representation of the probability simplex.

        Args:
            samples (np.array): Sampled weight vectors to plot.
            true_weights (np.array): true weight vectors (only for simulated data)
            labels (list[str]): Labels for each weight dimension.
            ax (plt.Axis): The pyplot axis.
        """

        if ax is None:
            ax = plt.gca()
        n_samples, n_p = samples.shape
        # color map
        cmap = sns.cubehelix_palette(light=1, start=.5, rot=-.75, as_cmap=True)
        if n_p == 2:
            #plt.title(str(feature), loc='center', fontdict={'fontweight': 'bold', 'fontsize': 20})
            x = samples.T[0]
            y = np.zeros(n_samples)
            sns.distplot(x, rug=True, hist=False, kde_kws={"shade": True, "lw": 0, "clip": (0, 1)}, color="g",
                         rug_kws={"color": "k", "alpha": 0.01, "height": 0.03})
            #sns.kdeplot(x, shade=True, color="r", clip=(0, 1))
            #plt.scatter(x, y, color='k', lw=0, s=1, alpha=0.2)
            #plt.axhline(y=0, color='k', linestyle='-', lw=0.5, xmin=0, xmax=1)
            plt.plot([0, 1], [0,0], c="k", lw=0.5)
            plt.xlim(0, 1)
            plt.axis('off')

            ax.axes.get_yaxis().set_visible(False)
            #ax.annotate('', xy=(0, -0.5), xytext=(1, -0.1),
            #            arrowprops=dict(arrowstyle="-", color='b'))

            if true_p is not None:
                plt.scatter(true_p[0], 0, color="#ed1696", lw=0, s=100, marker="*")

            if labels is not None:
                for x, label in enumerate(labels):
                    plt.text(x, -0.5, label, ha='center', va='top', fontdict={'fontsize': 16})

        elif n_p > 2:
        # Compute corners
            corners = Plot.get_corner_points(n_p)
            # Bounding box
            xmin, ymin = np.min(corners, axis=0)
            xmax, ymax = np.max(corners, axis=0)

            # Project the samples
            samples_projected = samples.dot(corners)

            # Density and scatter plot
            # plt.title(str(feature), loc='center', fontdict={'fontweight': 'bold', 'fontsize': 20})
            x = samples_projected.T[0]
            y = samples_projected.T[1]
            sns.kdeplot(x, y, shade=True, shade_lowest=True, cut=30, n_levels=100,
                    clip=([xmin, xmax], [ymin, ymax]), cmap=cmap)
            plt.scatter(x, y, color='k', lw=0, s=1, alpha=0.05)

            # Draw simplex and crop outside

            plt.fill(*corners.T, edgecolor='k', fill=False)
            Plot.fill_outside(corners, color='w', ax=ax)

            if true_p is not None:
                true_projected = true_p.dot(corners)
                plt.scatter(*true_projected.T, color="#ed1696", lw=0, s=100, marker="*")

            if labels is not None:
                for xy, label in zip(corners, labels):
                    xy *= 1.08  # Stretch, s.t. labels don't overlap with corners
                    plt.text(*xy, label, ha='center', va='center', fontdict={'fontsize': 16})

            plt.axis('equal')
            plt.axis('off')
            plt.tight_layout(0)

        plt.plot()
    # Find number of features
    # def find_num_features(self):
    #     for key in self.results['weights']:
    #         num = re.search('[0-9]+', key)
    #         if num:
    #             if int(num.group(0)) > self.number_features:
    #                 self.number_features = int(num.group(0))

    # Make a grid with all features (sorted by median contact)
    # By now we assume number of features to be 35; later this should be rewritten for any number of features
    # using find_num_features
    def plot_weights_grid(self, labels=None):

        weights, true_weights, _ = self.get_parameters(parameter="weights", b_in=5000)
        ordering = self.sort_by_weights(weights)

        n_plots = 4
        n_col = 4
        n_row = math.ceil(n_plots/n_col)

        fig, axs = plt.subplots(n_row, n_col, figsize=(15, 5))
        position = 1

        features = ordering[:n_plots]

        for f in features:
            plt.subplot(n_row, n_col, position)
            self.plot_weights(weights[f], feature=f, true_weights=true_weights[f], labels=labels)
            print(position, "of", n_plots, "plots finished")
            position += 1

        plt.subplots_adjust(wspace=0.2, hspace=0.2)
        fig.savefig(self.path_plots + '/weights_grid.pdf', dpi=400, format="pdf")

    # This is not changed yet
    def plot_probability_grid(self, p_name="gamma_a1", labels=None):
        """Creates a ridge plot for parameters with two states

       Args:
           samples (np.array): Sampled parameters
                shape(n_samples, 2)
           p_vec (str): name of parameter vector (either alpha, beta_familiy_* or gamma)
       """
        weights, true_weights, _ = self.get_parameters(parameter="weights", b_in=5000)
        ordering = self.sort_by_weights(weights)

        p, true_p, states = self.get_parameters(parameter=p_name, b_in=5000)

        n_plots = 4
        n_col = 4
        n_row = math.ceil(n_plots / n_col)

        fig, axs = plt.subplots(n_row, n_col, figsize=(15, 5))

        position = 1

        features = ordering[:n_plots]

        for f in features:

            plt.subplot(n_row, n_col, position)
            self.plot_probability_vectors(p[f], feature=f, true_p=true_p[f], labels=states[f])
            print(position, "of", n_plots, "plots finished")
            position += 1

        plt.subplots_adjust(wspace=0.2, hspace=0.2)

        fig.savefig(self.path_plots + '/prob_grid.pdf', dpi=400, format="pdf")

