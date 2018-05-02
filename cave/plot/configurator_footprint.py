#!/bin/python

__author__ = "Marius Lindauer & Joshua Marben"
__copyright__ = "Copyright 2016, ML4AAD"
__license__ = "BSD"
__maintainer__ = "Joshua Marben"
__email__ = "marbenj@cs.uni-freiburg.de"
__version__ = "0.0.1"

import os
import sys
import inspect
import logging
import json
import copy
import typing
import itertools
from collections import OrderedDict

from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter
import numpy as np
import scipy as sp
import pandas as pd
import sklearn
from scipy.spatial.distance import hamming
from sklearn.manifold.mds import MDS
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

from bokeh.plotting import figure, ColumnDataSource, show
from bokeh.embed import components
from bokeh.models import HoverTool, ColorBar, LinearColorMapper, BasicTicker, CustomJS, Slider, RadioGroup
from bokeh.models.sources import CDSView
from bokeh.models.filters import GroupFilter
from bokeh.layouts import row, column, widgetbox

import matplotlib as mpl
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from matplotlib.animation import FuncAnimation

import mpld3

cmd_folder = os.path.realpath(
    os.path.abspath(os.path.split(inspect.getfile(inspect.currentframe()))[0]))
cmd_folder = os.path.realpath(os.path.join(cmd_folder, ".."))
if cmd_folder not in sys.path:
    sys.path.append(cmd_folder)

from smac.scenario.scenario import Scenario
from smac.runhistory.runhistory import RunHistory, DataOrigin
from smac.optimizer.objective import average_cost
from smac.epm.rf_with_instances import RandomForestWithInstances
from smac.configspace import ConfigurationSpace, Configuration
from smac.utils.util_funcs import get_types
from ConfigSpace.util import impute_inactive_values
from ConfigSpace.hyperparameters import FloatHyperparameter, IntegerHyperparameter
from ConfigSpace import CategoricalHyperparameter, UniformFloatHyperparameter, UniformIntegerHyperparameter

from cave.utils.convert_for_epm import convert_data_for_epm
from cave.utils.helpers import escape_parameter_name
from cave.utils.timing import timing
from cave.utils.io import export_bokeh


class ConfiguratorFootprint(object):

    def __init__(self, scenario: Scenario,
                 runhistories: typing.List[RunHistory],
                 incs: list=None,
                 max_plot=None,
                 contour_step_size=0.2,
                 output_dir: str=None,
                 ):
        '''
        Constructor

        Parameters
        ----------
        scenario: Scenario
            scenario
        runhistories: List[RunHistory]
            runhistories from configurator runs - first one assumed to be best
        incs: list
            incumbents of best configurator run, last entry is final incumbent
        max_plot: int
            maximum number of configs to plot
        contour_step_size: float
            step size of meshgrid to compute contour of fitness landscape
        output_dir: str
            output directory
        '''
        self.logger = logging.getLogger(
            self.__module__ + '.' + self.__class__.__name__)

        self.scenario = copy.deepcopy(scenario)  # pca changes feats
        self.runhistories = runhistories
        # runs_per_rh holds a list for every rh in the order of self.runhistores
        # each list holds a list in turn with the number of
        #   configuration-evaluations in the order of conf-list
        #   for that rh after the fraction of total runs in that
        #   runhistory that corresponds to the index of the inner list, so for
        #   two runhistories with three configs and four quantiles thats:
        #     [
        #      # runhistory 1
        #      [[1, 2, 1], [3, 5, 2], [5, 6, 7], [9, 9, 8]],
        #      # runhistory 2
        #      [[2, 5, 10], [4, 6, 13], [7, 8, 14], [9, 9, 14]],
        #     ]
        #   so to access the full # runs for the best configurator-run, just
        #   go for self.runs_per_rh[0][-1]
        self.runs_per_rh = []
        self.conf_list = []
        self.conf_matrix = []
        self.incs = incs
        self.max_plot = max_plot
        self.max_rhs_to_plot = 1  # Maximum number of runhistories 2 b plotted

        self.contour_step_size = contour_step_size
        self.output_dir = output_dir if output_dir else None

    def run(self):
        """
        Uses available Configurator-data to perform a MDS, estimate performance
        data and plot the configurator footprint.

        Returns
        -------
        html_code: str
            html-embedded plot-data
        """

        self.get_conf_matrix()
        self.logger.debug("Number of Configurations: %d" %
                         (self.conf_matrix.shape[0]))
        dists = self.get_distance(self.conf_matrix, self.scenario.cs)
        red_dists = self.get_mds(dists)

        contour_data = self.get_pred_surface(
                X_scaled=red_dists, conf_list=self.conf_list[:])

        inc_list = self.incs

        return self.plot(red_dists, self.conf_list, self.runs_per_rh,
                         inc_list=inc_list, contour_data=contour_data)

    def get_pred_surface(self, X_scaled, conf_list: list):
        """fit epm on the scaled input dimension and
        return data to plot a contour plot

        Parameters
        ----------
        X_scaled: np.array
            configurations in scaled 2dim
        conf_list: list
            list of Configuration objects

        Returns
        -------
        np.array, np.array, np.array
            x,y,Z for contour plots
        """

        # use PCA to reduce features to also at most 2 dims
        n_feats = self.scenario.feature_array.shape[1]
        if n_feats > 2:
            self.logger.debug("Use PCA to reduce features to 2dim")
            insts = self.scenario.feature_dict.keys()
            feature_array = np.array([self.scenario.feature_dict[inst] for inst in insts])
            ss = StandardScaler()
            self.scenario.feature_array = ss.fit_transform(feature_array)
            pca = PCA(n_components=2)
            feature_array = pca.fit_transform(feature_array)
            n_feats = feature_array.shape[1]
            self.scenario.feature_array = feature_array
            self.scenario.feature_dict = dict([(inst, feature_array[idx,:]) for idx, inst in enumerate(insts)])
            self.scenario.n_features = 2

        # Create new rh with only wanted configs
        new_rh = RunHistory(average_cost)
        for rh in self.runhistories:
            for key, value in rh.data.items():
                config = rh.ids_config[key.config_id]
                if config in conf_list:
                    config_id, instance, seed = key
                    cost, time, status, additional_info = value
                    new_rh.add(config, cost, time, status, instance_id=instance,
                               seed=seed, additional_info=additional_info)
        self.relevant_rh = new_rh

        X, y, types = convert_data_for_epm(scenario=self.scenario,
                                           runhistory=new_rh,
                                           logger=self.logger)

        types = np.array(np.zeros((2+n_feats)), dtype=np.uint)

        num_params = len(self.scenario.cs.get_hyperparameters())

        # impute missing values in configs
        conf_dict = {}
        for idx, c in enumerate(conf_list):
            conf_list[idx] = impute_inactive_values(c)
            conf_dict[str(conf_list[idx].get_array())] = X_scaled[idx, :]

        X_trans = []
        for x in X:
            x_scaled_conf = conf_dict[str(x[:num_params])]
            x_new = np.concatenate(
                        (x_scaled_conf, x[num_params:]), axis=0)
            X_trans.append(x_new)
        X_trans = np.array(X_trans)

        bounds = np.array([(0, np.nan), (0, np.nan)], dtype=object)
        model = RandomForestWithInstances(types=types, bounds=bounds,
                                          instance_features=np.array(self.scenario.feature_array),
                                          ratio_features=1.0)

        model.train(X_trans, y)

        self.logger.debug("RF fitted")

        plot_step = self.contour_step_size

        x_min, x_max = X_scaled[:, 0].min() - 1, X_scaled[:, 0].max() + 1
        y_min, y_max = X_scaled[:, 1].min() - 1, X_scaled[:, 1].max() + 1
        xx, yy = np.meshgrid(np.arange(x_min, x_max, plot_step),
                             np.arange(y_min, y_max, plot_step))

        self.logger.debug("x_min: %f, x_max: %f, y_min: %f, y_max: %f" %(x_min, x_max, y_min, y_max))

        self.logger.debug("Predict on %d samples in grid to get surface" %(np.c_[xx.ravel(), yy.ravel()].shape[0]))
        Z, _ = model.predict_marginalized_over_instances(
            np.c_[xx.ravel(), yy.ravel()])

        Z = Z.reshape(xx.shape)

        return xx, yy, Z

    def get_distance(self, conf_matrix, cs: ConfigurationSpace):
        """
        Computes the distance between all pairs of configurations.

        Parameters
        ----------
        conf_matrx: np.array
            numpy array with cols as parameter values
        cs: ConfigurationSpace
            ConfigurationSpace to get conditionalities

        Returns
        -------
        dists: np.array
            np.array with distances between configurations i,j in dists[i,j] or dists[j,i]
        """
        n_confs = conf_matrix.shape[0]
        dists = np.zeros((n_confs, n_confs))

        is_cat = []
        depth = []
        for _, param in cs._hyperparameters.items():
            if type(param) == CategoricalHyperparameter:
                is_cat.append(True)
            else:
                is_cat.append(False)
            depth.append(self.get_depth(cs, param))
        is_cat = np.array(is_cat)
        depth = np.array(depth)

        for i in range(n_confs):
            for j in range(i + 1, n_confs):
                dist = np.abs(conf_matrix[i, :] - conf_matrix[j, :])
                dist[np.isnan(dist)] = 1
                dist[np.logical_and(is_cat, dist != 0)] = 1
                dist /= depth
                dists[i, j] = np.sum(dist)
                dists[j, i] = np.sum(dist)

        return dists

    def get_depth(self, cs: ConfigurationSpace, param: str):
        """
        Get depth in configuration space of a given parameter name
        breadth search until reaching a leaf for the first time

        Parameters
        ----------
        cs: ConfigurationSpace
            ConfigurationSpace to get parents of a parameter
        param: str
            name of parameter to inspect
        """
        parents = cs.get_parents_of(param)
        if not parents:
            return 1
        new_parents = parents
        d = 1
        while new_parents:
            d += 1
            old_parents = new_parents
            new_parents = []
            for p in old_parents:
                pp = cs.get_parents_of(p)
                if pp:
                    new_parents.extend(pp)
                else:
                    return d

    def get_mds(self, dists):
        """
        Compute multi-dimensional scaling (using sklearn MDS) -- nonlinear scaling

        Parameters
        ----------
        dists: np.array
            full matrix of distances between all configurations

        Returns
        -------
        np.array
            scaled coordinates in 2-dim room
        """

        mds = MDS(
            n_components=2, dissimilarity="precomputed", random_state=12345)
        return mds.fit_transform(dists)

    def get_conf_matrix(self):
        """
        Iterates through runhistories to get a matrix of configurations (in
        vector representation), a list of configurations and the number of
        runs per configuration in a quantiled manner.

        Sideeffect creates
        conf_matrix: np.array
            matrix of configurations in vector representation
        conf_list: list
            list of all Configuration objects that appeared in any runhistory
            the order of this list is used to determine all kinds of properties
            in the plotting
        runs_per_conf: np.array
            one-dim numpy array of runs per configuration
            FOR BEST RUNHISTORY ONLY
        """
        # Get all configurations. Index of c in conf_list serves as identifier
        for rh in self.runhistories:
            for c in rh.get_all_configs():
                if not c in self.conf_list:
                    self.conf_matrix.append(c.get_array())
                    self.conf_list.append(c)
        for inc in self.incs:
            if inc not in self.conf_list:
                self.conf_matrix.append(inc.get_array())
                self.conf_list.append(inc)

        # Get total runs per config per rh
        for rh in self.runhistories:
            # We want to visualize the development over time, so we take
            #   screenshots of the number of runs per config at different points
            #   in (i.e. different quantiles of) the runhistory, LAST quantile
            #   is full history!!
            r_p_q_p_c = self._get_runs_per_config_quantiled(rh, quantiles=10)
            self.runs_per_rh.append(np.array(r_p_q_p_c))
        # Get minimum and maximum for sizes of dots
        self.min_runs_per_conf = min([i for i in self.runs_per_rh[0][-1] if i > 0])
        self.max_runs_per_conf = max(self.runs_per_rh[0][-1])

        self.logger.debug("Gathered %d configurations from %d runhistories." %
                          (len(self.conf_list), len(self.runs_per_rh)))
        self.conf_matrix = np.array(self.conf_matrix)

    @timing
    def _get_runs_per_config_quantiled(self, rh, quantiles=10):
        """Creates a
        list of configurator-runs to be analyzed, each as a np.array with
        the number of target-algorithm-runs per config per quantile.
        two runhistories with three configs and four quantiles thats:
          [
           # runhistory 1
           [[1, 2, 1], [3, 5, 2], [5, 6, 7], [9, 9, 8]],
           # runhistory 2
           [[2, 5, 10], [4, 6, 13], [7, 8, 14], [9, 9, 14]],
          ]

        Parameters
        ----------
        rh: RunHistory
            rh to evaluate
        quantiles: int
            number of fractions to split rh into

        Returns:
        --------
        runs_per_config: Dict[Configuration : int]
            number of runs for config in rh up to given time
        """
        runs_total = len(rh.data)
        step = int(runs_total / quantiles)
        self.logger.debug("Creating %d quantiles with a step of %d and a total "
                          "runs of %d", quantiles, step, runs_total)
        r_p_q_p_c = []  # runs per quantile per config
        tmp_rh = RunHistory(average_cost)
        as_list = list(rh.data.items())
        ranges = [0] + list(range(step, runs_total-step, step)) + [runs_total]

        for i, j in zip(ranges[:-1], ranges[1:]):
            for idx in range(i, j):
                k, v = as_list[idx]
                tmp_rh.add(config=rh.ids_config[k.config_id],
                           cost=v.cost, time=v.time, status=v.status,
                           instance_id=k.instance_id, seed=k.seed)
            r_p_q_p_c.append([len(tmp_rh.get_runs_for_config(c)) for c in
                self.conf_list])
            #self.logger.debug("Using %d of %d runs", len(tmp_rh.data), len(rh.data))
        return r_p_q_p_c

    def _get_size(self, r_p_c):
        self.logger.debug("Min runs per conf: %d, Max runs per conf: %d",
                          self.min_runs_per_conf, self.max_runs_per_conf)
        normalization_factor = self.max_runs_per_conf - self.min_runs_per_conf
        if normalization_factor == 0:  # All configurations same size
            normalization_factor = 1
        sizes = 5 + ((r_p_c - self.min_runs_per_conf) / normalization_factor) * 20
        sizes *= np.array([0 if r == 0 else 1 for r in r_p_c])  # 0 size if 0 runs
        return sizes

    def _get_color(self, cds):
        """
        Parameters:
        -----------
        cds: ColumnDataSource
            data for bokeh plot

        Returns:
        --------
        colors: list
            list of color per config
        """
        colors = []
        for t in cds.data['type']:
            if t == "Default":
                colors.append('orange')
            elif "Incumbent" in  t:
                colors.append('red')
            else:
                colors.append('white')
        return colors

    def _plot_contour(self, p, contour_data, x_range, y_range):
        """Plot contour data.

        Parameters
        ----------
        p: bokeh.plotting.figure
            figure to be drawn upon
        contour_data: np.array
            array with contour data
        x_range: List[float, float]
            min and max of x-axis
        y_range: List[float, float]
            min and max of y-axis

        Returns
        -------
        p: bokeh.plotting.figure
            modified figure handle
        """
        min_z = np.min(np.unique(contour_data[2]))
        max_z = np.max(np.unique(contour_data[2]))
        color_mapper = LinearColorMapper(palette="Viridis256",
                                         low=min_z, high=max_z)
        p.image(image=contour_data, x=x_range[0], y=y_range[0],
                dw=x_range[1] - x_range[0], dh=y_range[1] - y_range[0],
                color_mapper=color_mapper)
        color_bar = ColorBar(color_mapper=color_mapper,
                             ticker=BasicTicker(desired_num_ticks=15),
                             label_standoff=12,
                             border_line_color=None, location=(0,0))
        color_bar.major_label_text_font_size = '12pt'
        p.add_layout(color_bar, 'right')
        return p

    def _plot_create_views(self, source):
        """Create views in order of plotting, so more interesting views are
        plotted on top. Order of interest:
        default > final-incumbent > incumbent > candidate
          local > random
            num_runs (ascending, more evaluated -> more interesting)
        Individual views are necessary, since bokeh can only plot one
        marker-type per 'scatter'-call

        Parameters
        ----------
        source: ColumnDataSource
            containing relevant information for plotting

        Returns
        -------
        views: List[CDSView]
            views in order of plotting
        markers: List[string]
            markers (to the view with the same index)
        """

        def _get_marker(t, o):
            """ returns marker according to type t and origin o """
            if t == "Default":
                shape = 'triangle'
            elif t == 'Final Incumbent':
                shape = 'inverted_triangle'
            else:
                shape = 'square' if t == "Incumbent" else 'circle'
                shape += '_x' if o.startswith("Acquisition Function") else ''
            return shape

        views, markers = [], []
        for t in ['Candidate', 'Incumbent', 'Final Incumbent', 'Default']:
            for o in ['Unknown', 'Random', 'Acquisition Function']:
                for z in sorted(list(set(source.data['zorder'])),
                                key=lambda x: int(x)):
                    views.append(CDSView(source=source, filters=[
                            GroupFilter(column_name='type', group=t),
                            GroupFilter(column_name='origin', group=o),
                            GroupFilter(column_name='zorder', group=z)]))
                    markers.append(_get_marker(t, o))
        self.logger.debug("%d different glyph renderers", len(views))
        self.logger.debug("%d different zorder-values", len(set(source.data['zorder'])))

    def _plot_scatter(self, p, source, views, markers):
        """
        Parameters
        ----------
        p: bokeh.plotting.figure
            figure
        source: ColumnDataSource
            data container
        views: List[CDSView]
            list with views to be plotted (in order!)
        markers: List[str]
            corresponding markers to the views

        Returns
        -------
        scatter_handles: List[GlyphRenderer]
            glyph renderer per view
        """
        scatter_handles = []
        for view, marker in zip(views, markers):
            scatter_handles.append(p.scatter(x='x', y='y',
                                   source=source,
                                   view=view,
                                   color='color', line_color='black',
                                   size='size',
                                   marker=marker,
                                   ))

        p.xaxis.axis_label = "MDS-X"
        p.yaxis.axis_label = "MDS-Y"
        p.xaxis.axis_label_text_font_size = "15pt"
        p.yaxis.axis_label_text_font_size = "15pt"
        p.xaxis.major_label_text_font_size = "12pt"
        p.yaxis.major_label_text_font_size = "12pt"
        p.title.text_font_size = "15pt"
        p.legend.label_text_font_size = "15pt"
        return scatter_handles

    def plot(self, X, conf_list: list, configurator_runs, runs_labels=None,
             inc_list: list=[], contour_data=None):
        """
        plots sampled configuration in 2d-space;
        uses bokeh for interactive plot
        saves results in self.output, if set

        Parameters
        ----------
        X: np.array
            np.array with 2-d coordinates for each configuration
        conf_list: list
            list of ALL configurations in the same order as X
        configurator_runs: list[np.array]
            list of configurator-runs to be analyzed, each as a np.array with
            the number of target-algorithm-runs per config per quantile.
        runs_labels: list[str]
            labels for the individual configurator-runs. if None, they are
            enumerated
        inc_list: list
            list of incumbents (Configuration)
        contour_data: list
            contour data (xx,yy,Z)

        Returns
        -------
        html_script: str
            HTML script representing the visualization
        """
        if not runs_labels:  # TODO We only plot first run atm anyway, this will be used later
            runs_labels = range(len(configurator_runs))

        hp_names = [k.name for k in  # Hyperparameter names
                    conf_list[0].configuration_space.get_hyperparameters()]

        # Create ColumnDataSource with all the necessary data
        #   Contains for each configuration evaluated on any run:
        #   - all parameters and values
        #   - origin (if conflicting, origin from best run counts)
        #   - type (default, incumbent or candidate)
        #   - # of runs
        #   - size
        #   - color
        source = ColumnDataSource(data=dict(x=X[:, 0], y=X[:, 1]))
        for k in hp_names:  # Add parameters for each config
            source.add([c[k] if c[k] else "None" for c in conf_list],
                       escape_parameter_name(k))
        # TODO differentiate between different configurator-runs below
        default = conf_list[0].configuration_space.get_default_configuration()
        conf_types = ["Default" if c == default else "Final Incumbent" if c == inc_list[-1]
                      else "Incumbent" if c in inc_list else "Candidate" for c in conf_list]
        # We group "Local Search" and "Random Search (sorted)" both into local
        origins = [self._get_config_origin(c) for c in conf_list]
        source.add(conf_types, 'type')
        source.add(origins, 'origin')
        sizes = self._get_size(configurator_runs[0][-1])  # 0 for best run, -1 for full history
        sizes = [s * 3 if conf_types[idx] == "Default" else s for idx, s in enumerate(sizes)]
        source.add(sizes, 'size')
        source.add(self._get_color(source), 'color')
        source.add(configurator_runs[0][-1], 'runs')  # 0 for best run, -1 for full history
        for idx, q in enumerate(configurator_runs[0]):
            source.add(q, 'runs'+str(idx))
        for idx, q in enumerate(configurator_runs[0]):
            source.add(self._get_size(q), 'size'+str(idx))


        # To enforce zorder, we categorize all entries according to their size
        # Since we plot all different zorder-levels sequentially, we use a
        # manually defined level of influence
        num_bins = 20  # How fine-grained the size-ordering should be
        min_size, max_size = min(source.data['size']), max(source.data['size'])
        step_size = (max_size - min_size) / num_bins
        zorder = [str(int((s - min_size) / step_size)) for s in source.data['size']]
        source.add(zorder, 'zorder')  # string, so we can apply group filter

        # Define what appears in tooltips
        # TODO add only important parameters (needs to change order of exec:
        #                                     pimp before conf-footprints)
        hover = HoverTool(tooltips=[('type', '@type'), ('origin', '@origin'), ('runs', '@runs')] +
                                   [(k, '@' + escape_parameter_name(k)) for k in hp_names])

        # bokeh-figure
        x_range = [min(X[:, 0]) - 1, max(X[:, 0]) + 1]
        y_range = [min(X[:, 1]) - 1, max(X[:, 1]) + 1]
        p = figure(plot_height=500, plot_width=600,
                   tools=[hover], x_range=x_range, y_range=y_range)

        # Plot contour
        if contour_data is not None:
           p = self._plot_contour(p, contour_data, x_range, y_range)

        # Create views from source
        views, markers = self._plot_create_views(source)

        # Scatter
        scatters = self._plot_scatter(p, source, views, markers)

        line_list = ['line' + str(i) for i in range(len(scatters))]
        #checkbox = RadioGroup(labels=line_list, active=0)
        num_quantiles = len(configurator_runs[0])
        checkbox = Slider(start=1, end=len(line_list), value=num_quantiles, step=1)
        #line_list = "[" + ",".join(['line' + str(i) for i in range(len(scatters))]) + "]"
        code = "line_list = [" + ','.join(line_list) + '];' + """
lab_len=cb_obj.end;

for (i=0;i<lab_len;i++) {
    if (cb_obj.value == i) {
        line_list[i].visible = true;
        console.log('Setting to true: ' + i)
    } else {
        line_list[i].visible = false;
        console.log('Setting to false: ' + i)
    }
}
"""
        self.logger.info(code)
        args = {k : v for k, v in zip(['line' + str(i) for i in
            range(len(line_list))], scatters)}
        print(args)
        checkbox.callback = CustomJS(args=args, code=code)
        #dict(line0=scatters[0], line1=scatters[1]), code=code)

        # Now we want to integrate a slider to visualize development over time
        # Since runhistory doesn't contain a timestamp, but are ordered, we use quantiles
        # Some js-script that updates the bokeh-plot on callback
        code = """
var data = source.data;
var time = time.value;
data['runs'] = data['runs'+time.toString()]
data['size'] = data['size'+time.toString()]

source.change.emit();
"""
        # Create callback and slider itself
        callback = CustomJS(args=dict(source=source), code=code)

        num_quantiles = len(configurator_runs[0])
        time_slider = Slider(start=1, end=num_quantiles, value=num_quantiles, step=1,
                            title="Time", callback=callback)
        callback.args["time"] = time_slider

        # Slider below plot
        layout = column(p, widgetbox(time_slider, checkbox))

        script, div = components(layout)

        if self.output_dir:
            path = os.path.join(self.output_dir, "configurator_footprint.png")
            export_bokeh(p, path, self.logger)

        return script, div

    def _get_config_origin(self, c):
        """Return appropriate configuration origin

        Parameters
        ----------
        c: Configuration
            configuration to be examined

        Returns
        -------
        origin: str
            origin of configuration (e.g. "Local", "Random", etc.)
        """
        origin = "Unknown"
        if c.origin.startswith("Local") or "sorted" in c.origin:
            origin = "Acquisition Function"
        elif c.origin.startswith("Random"):
            origin = "Random"
        return origin
