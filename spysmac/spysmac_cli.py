#!/usr/bin/env python

from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter
import logging
import os
import sys
import shutil

from spysmac.analyzer import Analyzer
from spysmac.html.html_builder import HTMLBuilder

from smac.runhistory.runhistory import RunHistory
from smac.scenario.scenario import Scenario
from smac.utils.io.cmd_reader import CMDReader


__author__ = "Joshua Marben"
__copyright__ = "Copyright 2017, ML4AAD"
__license__ = "3-clause BSD"
__maintainer__ = "Joshua Marben"
__email__ = "joshua.marben@neptun.uni-freiburg.de"

class SpySMACCLI(object):
    """
    SpySMAC command line interface.
    """

    def main_cli(self):
        """
        Main cli, implementing comparison between and analysis of SMAC-results.
        """
        parser = ArgumentParser(formatter_class=ArgumentDefaultsHelpFormatter)
        req_opts = parser.add_argument_group("Required Options")
        req_opts.add_argument("--folders", required=True, nargs='+',
                              help="path(s) to SMAC output-directory/ies, "
                                   "containing each at least a runhistory and "
                                   "a trajectory.")

        opt_opts = parser.add_argument_group("Optional Options")
        opt_opts.add_argument("--verbose_level", default="INFO",
                              choices=["INFO", "DEBUG"],
                              help="verbose level")
        opt_opts.add_argument("--missing_data_method", default="validation",
                              choices=["validation", "epm"],
                              help="how to complete missing runs for "
                                   "config/inst-pairs.")
        opt_opts.add_argument("--output", default="SpySMAC_output",
                              help="path to folder in which to save the HTML-report.")
        opt_opts.add_argument("--ta_exec_dir", default='.',
                              help="path to the execution-directory of the "
                                   "SMAC run.")
        opt_opts.add_argument("--param_importance", default=True,
                              help="whether to calculate (time-intensive) "
                                   "parameter importance.")
        args_, misc = parser.parse_known_args()

        if args_.verbose_level == "INFO":
            logging.basicConfig(level=logging.INFO)
        else:
            logging.basicConfig(level=logging.DEBUG)

        # SMAC results
        analyzer = Analyzer(args_.folders, args_.output, args_.ta_exec_dir,
                            missing_data_method=args_.missing_data_method)
        if args_.param_importance not in ('no', 'false', 'False', 'f', 'n', '0'):
            analyzer.analyze()
        else:
            analyzer.analyze(ablation=False, forward_selection=False, fanova=False)
        analyzer.build_html()
