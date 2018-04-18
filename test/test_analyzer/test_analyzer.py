import os
import numpy as np
import logging
import shutil

from nose.plugins.attrib import attr
import unittest

from smac.optimizer.objective import average_cost
from smac.scenario.scenario import Scenario
from smac.runhistory.runhistory import RunHistory
from smac.utils.io.traj_logging import TrajLogger
from smac.utils.validate import Validator

from cave.analyzer import Analyzer
from cave.cavefacade import CAVE
from cave.plot.plotter import Plotter


class TestAnalyzer(unittest.TestCase):

    def setUp(self):
        self.output = "test/test_files/test_output/"
        shutil.rmtree(self.output, ignore_errors=True)
        self.cave = CAVE(["examples/smac3/example_output/run_1",
                          "examples/smac3/example_output/run_2",
                          "examples/smac3/example_output/run_3"],
                         output=self.output,
                         missing_data_method="epm",
                         ta_exec_dir="examples/smac3")
        self.analyzer = self.cave.analyzer

    def test_confviz(self):
        """ testing configuration visualization """
        self.analyzer.plot_confviz(runhistories=[self.analyzer.original_rh],
                                   incumbents=[self.analyzer.incumbent])

    @attr('slow')
    def test_fanova(self):
        """ testing configuration visualization """
        self.analyzer.fanova(incumbent=self.analyzer.incumbent)

    @attr('slow')
    def test_feature_forward_selection(self):
        """ testing feature importance """
        self.analyzer.feature_importance()

    def test_algorithm_footprints(self):
        """ testing algorithm footprints """
        self.analyzer.plot_algorithm_footprint()
