import logging
import time
import os
import vtk
import re 
import qt
import ctk

import csv
import itertools
import random
import math

import slicer
from slicer.i18n import tr as _
from slicer.i18n import translate
from slicer.ScriptedLoadableModule import *
from slicer.util import VTKObservationMixin
from slicer.parameterNodeWrapper import parameterNodeWrapper

from slicer import vtkMRMLScalarVolumeNode, vtkMRMLSegmentationNode
from typing import Annotated
from slicer.parameterNodeWrapper import parameterNodeWrapper, Choice, WithinRange
#
from SmoothingLib.file_utils import FileUtils
from SmoothingLib.segmentation_utils import SegmentationUtils
from SmoothingLib.smoothing_logic import SmoothingEngine
from SmoothingLib.batch_processor import BatchProcessor
from SmoothingLib.experiment_processor import ExperimentProcessor
from SmoothingLib.metrics_processor import MetricsProcessor
from SmoothingLib.constants import * 
# Smoothing
#


class Smoothing(ScriptedLoadableModule):
    """GUI-based segmentation smoothing module for 3D Slicer."""

    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)

        self.parent.title = _("Smoothing")
        self.parent.categories = [translate("qSlicerAbstractCoreModule", "Segmentation")]
        self.parent.dependencies = ["Segmentations", "SegmentEditor"]
        self.parent.contributors = ["Ben"]

        self.parent.helpText = _("""
Smoothing provides GUI-based postprocessing tools for smoothing 3D Slicer segmentations.
This version allows the user to select one or more smoothing methods from the GUI.
Each selected method is applied independently to a copy of the original segmentation.""")

        self.parent.acknowledgementText = _("""
This module was developed as a 3D Slicer scripted extension for segmentation postprocessing.
""")

@parameterNodeWrapper
class SmoothingParameterNode:
    """Parameters for GUI-based segmentation smoothing."""

    inputSegmentation: vtkMRMLSegmentationNode
    referenceVolume: vtkMRMLScalarVolumeNode
    outputSegmentation: vtkMRMLSegmentationNode

    applyScope: Annotated[
        str,
        Choice(["VISIBLE_SEGMENTS", "ALL_SEGMENTS"])
    ] = "VISIBLE_SEGMENTS"

    medianEnabled: bool = False
    openingEnabled: bool = False
    closingEnabled: bool = False
    gaussianEnabled: bool = False
    jointTaubinEnabled: bool = True

    medianKernelSizeMm: Annotated[float, WithinRange(0.1, 20.0)] = 3.0
    openingKernelSizeMm: Annotated[float, WithinRange(0.1, 20.0)] = 3.0
    closingKernelSizeMm: Annotated[float, WithinRange(0.1, 20.0)] = 3.0
    gaussianStandardDeviationMm: Annotated[float, WithinRange(0.1, 10.0)] = 1.0
    jointTaubinSmoothingFactor: Annotated[float, WithinRange(0.01, 1.0)] = 0.5

    overwriteInput: bool = False

class SmoothingWidget(ScriptedLoadableModuleWidget, VTKObservationMixin):
    """Module GUI."""

    def __init__(self, parent=None) -> None:
        ScriptedLoadableModuleWidget.__init__(self, parent)
        VTKObservationMixin.__init__(self)

        self.logic = None
        self._parameterNode = None
        self._parameterNodeGuiTag = None

    def setup(self) -> None:
        """
        Build the module GUI.

        The base .ui file contains the core single-case controls.
        Additional advanced modes are added programmatically below it:
            - Batch processing
            - Experiment mode
            - Metrics analysis
            - Progress/status
        """

        ScriptedLoadableModuleWidget.setup(self)

        # ------------------------------------------------------------
        # 1) Load base UI
        # ------------------------------------------------------------
        uiWidget = slicer.util.loadUI(self.resourcePath("UI/Smoothing.ui"))
        self.layout.addWidget(uiWidget)

        self.ui = slicer.util.childWidgetVariables(uiWidget)
        uiWidget.setMRMLScene(slicer.mrmlScene)

        self.logic = SmoothingLogic()

        self.addObserver(
            slicer.mrmlScene,
            slicer.mrmlScene.StartCloseEvent,
            self.onSceneStartClose,
        )
        self.addObserver(
            slicer.mrmlScene,
            slicer.mrmlScene.EndCloseEvent,
            self.onSceneEndClose,
        )

        # ------------------------------------------------------------
        # 2) Pack base UI controls
        # ------------------------------------------------------------
        self.packBaseGui()

        # ------------------------------------------------------------
        # 3) Add advanced workflow sections
        # ------------------------------------------------------------
        self.setupBatchGui()
        self.setupExperimentGui()
        self.setupMetricsGui()
        self.setupProgressGui()

        # ------------------------------------------------------------
        # 4) Defaults, connections, parameter node
        # ------------------------------------------------------------
        self.setupGuiDefaults()
        self.setupConnections()

        self.initializeParameterNode()

        self.layout.addStretch(1)
    
    def createHorizontalLine(self):
        """Create a visual separator line."""

        line = qt.QFrame()
        line.setFrameShape(qt.QFrame.HLine)
        line.setFrameShadow(qt.QFrame.Sunken)
        return line

    def packBaseGui(self) -> None:
        """
        Improve spacing and readability of the base UI loaded from Smoothing.ui.

        This assumes the .ui file already contains:
            - input segmentation selector
            - reference volume selector
            - output segmentation selector
            - method checkboxes
            - parameter tabs
            - apply button
            - status label
        """

        # ------------------------------------------------------------
        # Make method checkboxes compact if they exist in the UI
        # ------------------------------------------------------------
        methodCheckboxes = [
            getattr(self.ui, "medianCheckBox", None),
            getattr(self.ui, "openingCheckBox", None),
            getattr(self.ui, "closingCheckBox", None),
            getattr(self.ui, "gaussianCheckBox", None),
            getattr(self.ui, "jointTaubinCheckBox", None),
        ]

        for checkbox in methodCheckboxes:
            if checkbox is not None:
                checkbox.setSizePolicy(
                    qt.QSizePolicy.Preferred,
                    qt.QSizePolicy.Fixed,
                )

        # ------------------------------------------------------------
        # Make selector widgets expand horizontally
        # ------------------------------------------------------------
        selectorNames = [
            "inputSegmentationSelector",
            "referenceVolumeSelector",
            "outputSegmentationSelector",
        ]

        for selectorName in selectorNames:
            if hasattr(self.ui, selectorName):
                selector = getattr(self.ui, selectorName)
                selector.setSizePolicy(
                    qt.QSizePolicy.Expanding,
                    qt.QSizePolicy.Fixed,
                )

        # ------------------------------------------------------------
        # Parameter tabs should expand only horizontally, not vertically too much
        # ------------------------------------------------------------
        if hasattr(self.ui, "parametersTabWidget"):
            self.ui.parametersTabWidget.setSizePolicy(
                qt.QSizePolicy.Expanding,
                qt.QSizePolicy.Fixed,
            )

        # ------------------------------------------------------------
        # Status label
        # ------------------------------------------------------------
        if hasattr(self.ui, "statusLabel"):
            self.ui.statusLabel.wordWrap = True
            self.ui.statusLabel.setStyleSheet("color: gray;")
            
    def createFolderSelectorRow(self, lineEdit, buttonText="Browse..."):
        """
        Create a compact folder selector row:
            [ expanding line edit ][ fixed browse button ]
        """

        browseButton = qt.QPushButton(buttonText)

        lineEdit.setSizePolicy(
            qt.QSizePolicy.Expanding,
            qt.QSizePolicy.Fixed,
        )

        browseButton.setSizePolicy(
            qt.QSizePolicy.Fixed,
            qt.QSizePolicy.Fixed,
        )

        rowLayout = qt.QHBoxLayout()
        rowLayout.setContentsMargins(0, 0, 0, 0)
        rowLayout.setSpacing(6)
        rowLayout.addWidget(lineEdit)
        rowLayout.addWidget(browseButton)

        return rowLayout, browseButton


    def createSectionDescription(self, text):
        """Create a small wrapped explanatory label for each section."""

        label = qt.QLabel(text)
        label.wordWrap = True
        label.setStyleSheet("color: gray;")
        return label


    def setSectionEnabled(self, widget, enabled):
        """
        Enable/disable all children of a container, but keep the section itself visible.
        """

        children = widget.findChildren(qt.QWidget)

        for child in children:
            child.enabled = enabled
            
    def setupProgressGui(self) -> None:
        """
        Add compact status and progress controls.
        """

        self.progressCollapsibleButton = ctk.ctkCollapsibleButton()
        self.progressCollapsibleButton.text = "5. Progress"
        self.progressCollapsibleButton.collapsed = False

        # Put near the bottom, before the final spacer if possible.
        self.layout.addWidget(self.progressCollapsibleButton)

        progressLayout = qt.QVBoxLayout(self.progressCollapsibleButton)
        progressLayout.setContentsMargins(8, 8, 8, 8)
        progressLayout.setSpacing(6)

        self.progressStatusLabel = qt.QLabel("Ready.")
        self.progressStatusLabel.wordWrap = True
        self.progressStatusLabel.setStyleSheet("color: gray;")
        progressLayout.addWidget(self.progressStatusLabel)

        self.batchProgressBar = qt.QProgressBar()
        self.batchProgressBar.minimum = 0
        self.batchProgressBar.maximum = 100
        self.batchProgressBar.value = 0
        self.batchProgressBar.textVisible = True
        self.batchProgressBar.setSizePolicy(
            qt.QSizePolicy.Expanding,
            qt.QSizePolicy.Fixed,
        )
        progressLayout.addWidget(self.batchProgressBar)
        
    def setupBatchGui(self) -> None:
        """
        Add compact batch-processing controls.

        Batch mode uses:
            Volume_XXX.*
            Segmentation_XXX.*
        and saves one output segmentation per selected smoothing method.
        """

        self.batchCollapsibleButton = ctk.ctkCollapsibleButton()
        self.batchCollapsibleButton.text = "2. Batch processing"
        self.batchCollapsibleButton.collapsed = True
        self.layout.addWidget(self.batchCollapsibleButton)

        batchLayout = qt.QVBoxLayout(self.batchCollapsibleButton)
        batchLayout.setContentsMargins(8, 8, 8, 8)
        batchLayout.setSpacing(6)

        batchLayout.addWidget(
            self.createSectionDescription(
                "Process all matched Volume_XXX / Segmentation_XXX pairs from a folder. "
                "Each selected smoothing method is applied independently."
            )
        )

        self.batchModeCheckBox = qt.QCheckBox("Enable batch processing")
        self.batchModeCheckBox.checked = False
        batchLayout.addWidget(self.batchModeCheckBox)

        formLayout = qt.QFormLayout()
        formLayout.setContentsMargins(0, 0, 0, 0)
        formLayout.setSpacing(6)
        batchLayout.addLayout(formLayout)

        self.batchInputFolderLineEdit = qt.QLineEdit()
        self.batchInputFolderLineEdit.placeholderText = (
            "Folder containing Volume_XXX and Segmentation_XXX files"
        )
        inputFolderLayout, self.batchInputFolderButton = self.createFolderSelectorRow(
            self.batchInputFolderLineEdit
        )
        formLayout.addRow("Input folder:", inputFolderLayout)

        self.batchOutputFolderLineEdit = qt.QLineEdit()
        self.batchOutputFolderLineEdit.placeholderText = (
            "Folder where smoothed segmentations will be saved"
        )
        outputFolderLayout, self.batchOutputFolderButton = self.createFolderSelectorRow(
            self.batchOutputFolderLineEdit
        )
        formLayout.addRow("Output folder:", outputFolderLayout)

        optionsLayout = qt.QGridLayout()
        optionsLayout.setContentsMargins(0, 0, 0, 0)
        optionsLayout.setSpacing(6)
        batchLayout.addLayout(optionsLayout)

        self.batchRecursiveCheckBox = qt.QCheckBox("Search recursively")
        self.batchRecursiveCheckBox.checked = True

        self.batchKeepLoadedNodesCheckBox = qt.QCheckBox("Keep loaded nodes in scene")
        self.batchKeepLoadedNodesCheckBox.checked = False

        optionsLayout.addWidget(self.batchRecursiveCheckBox, 0, 0)
        optionsLayout.addWidget(self.batchKeepLoadedNodesCheckBox, 0, 1)

        batchLayout.addWidget(self.createHorizontalLine())

        # Connections
        self.batchModeCheckBox.connect("toggled(bool)", self.onBatchModeChanged)
        self.batchInputFolderButton.connect("clicked(bool)", self.onBrowseBatchInputFolder)
        self.batchOutputFolderButton.connect("clicked(bool)", self.onBrowseBatchOutputFolder)
        self.batchInputFolderLineEdit.connect("textChanged(QString)", self._checkCanApply)
        self.batchOutputFolderLineEdit.connect("textChanged(QString)", self._checkCanApply)
        self.batchRecursiveCheckBox.connect("toggled(bool)", self._checkCanApply)
        self.batchKeepLoadedNodesCheckBox.connect("toggled(bool)", self._checkCanApply)
    
    def setupExperimentGui(self) -> None:
        """
        Add experiment controls.

        Experiment mode depends on batch mode because it processes folders.
        It generates multiple smoothing runs from parameter values.
        """

        self.experimentCollapsibleButton = ctk.ctkCollapsibleButton()
        self.experimentCollapsibleButton.text = "3. Experiment / parameter exploration"
        self.experimentCollapsibleButton.collapsed = True
        self.layout.addWidget(self.experimentCollapsibleButton)

        experimentLayout = qt.QVBoxLayout(self.experimentCollapsibleButton)
        experimentLayout.setContentsMargins(8, 8, 8, 8)
        experimentLayout.setSpacing(6)

        experimentLayout.addWidget(
            self.createSectionDescription(
                "Run multiple smoothing configurations over all batch cases. "
                "Use full factorial for systematic sweeps or Monte Carlo for random exploration."
            )
        )

        self.experimentModeCheckBox = qt.QCheckBox("Enable experiment mode")
        self.experimentModeCheckBox.checked = False
        experimentLayout.addWidget(self.experimentModeCheckBox)

        generalForm = qt.QFormLayout()
        generalForm.setContentsMargins(0, 0, 0, 0)
        generalForm.setSpacing(6)
        experimentLayout.addLayout(generalForm)

        self.experimentTypeComboBox = qt.QComboBox()
        self.experimentTypeComboBox.addItem("Full factorial")
        self.experimentTypeComboBox.addItem("Monte Carlo")
        generalForm.addRow("Experiment type:", self.experimentTypeComboBox)

        self.monteCarloRunsSpinBox = qt.QSpinBox()
        self.monteCarloRunsSpinBox.minimum = 1
        self.monteCarloRunsSpinBox.maximum = 10000
        self.monteCarloRunsSpinBox.value = 20
        generalForm.addRow("Monte Carlo runs:", self.monteCarloRunsSpinBox)

        self.randomSeedSpinBox = qt.QSpinBox()
        self.randomSeedSpinBox.minimum = 0
        self.randomSeedSpinBox.maximum = 999999
        self.randomSeedSpinBox.value = 42
        generalForm.addRow("Random seed:", self.randomSeedSpinBox)

        self.experimentInfoLabel = qt.QLabel()
        self.experimentInfoLabel.wordWrap = True
        self.experimentInfoLabel.setStyleSheet("color: gray;")
        experimentLayout.addWidget(self.experimentInfoLabel)

        # Parameter group
        parameterGroupBox = qt.QGroupBox("Parameter values")
        parameterGroupLayout = qt.QFormLayout(parameterGroupBox)
        parameterGroupLayout.setContentsMargins(8, 8, 8, 8)
        parameterGroupLayout.setSpacing(6)
        experimentLayout.addWidget(parameterGroupBox)

        self.medianExperimentLineEdit = qt.QLineEdit("1,2,3,5")
        self.openingExperimentLineEdit = qt.QLineEdit("1,2,3,5")
        self.closingExperimentLineEdit = qt.QLineEdit("1,2,3,5")
        self.gaussianExperimentLineEdit = qt.QLineEdit("0.5,1.0,1.5,2.0")
        self.jointTaubinExperimentLineEdit = qt.QLineEdit("0.2,0.4,0.6,0.8")

        parameterGroupLayout.addRow("Median kernel [mm]:", self.medianExperimentLineEdit)
        parameterGroupLayout.addRow("Opening kernel [mm]:", self.openingExperimentLineEdit)
        parameterGroupLayout.addRow("Closing kernel [mm]:", self.closingExperimentLineEdit)
        parameterGroupLayout.addRow("Gaussian sigma [mm]:", self.gaussianExperimentLineEdit)
        parameterGroupLayout.addRow("Joint Taubin factor:", self.jointTaubinExperimentLineEdit)

        experimentLayout.addWidget(self.createHorizontalLine())

        # Connections
        self.experimentModeCheckBox.connect("toggled(bool)", self.onExperimentModeChanged)
        self.experimentTypeComboBox.connect("currentIndexChanged(int)", self.onExperimentTypeChanged)

        self.monteCarloRunsSpinBox.connect("valueChanged(int)", self._checkCanApply)
        self.randomSeedSpinBox.connect("valueChanged(int)", self._checkCanApply)

        self.medianExperimentLineEdit.connect("textChanged(QString)", self._checkCanApply)
        self.openingExperimentLineEdit.connect("textChanged(QString)", self._checkCanApply)
        self.closingExperimentLineEdit.connect("textChanged(QString)", self._checkCanApply)
        self.gaussianExperimentLineEdit.connect("textChanged(QString)", self._checkCanApply)
        self.jointTaubinExperimentLineEdit.connect("textChanged(QString)", self._checkCanApply)

        self.onExperimentTypeChanged()
        self.onExperimentModeChanged()

    def isMetricsModeEnabled(self) -> bool:
        return (
            hasattr(self, "metricsModeCheckBox")
            and self.metricsModeCheckBox.checked
        )


    def onMetricsModeChanged(self, checked=False) -> None:
        """
        Metrics mode is independent from smoothing and experiment execution.

        When enabled, normal batch/experiment inputs are not required.
        """

        metricsMode = self.isMetricsModeEnabled()

        if metricsMode:
            self.batchModeCheckBox.checked = False
            self.experimentModeCheckBox.checked = False

        self._checkCanApply()


    def onBrowseMetricsDataFolder(self, checked=False) -> None:
        folderPath = qt.QFileDialog.getExistingDirectory(
            slicer.util.mainWindow(),
            "Select original data folder",
            self.metricsDataFolderLineEdit.text,
        )

        if folderPath:
            self.metricsDataFolderLineEdit.text = folderPath
            self._checkCanApply()


    def onBrowseMetricsOutputFolder(self, checked=False) -> None:
        folderPath = qt.QFileDialog.getExistingDirectory(
            slicer.util.mainWindow(),
            "Select experiment output folder",
            self.metricsOutputFolderLineEdit.text,
        )

        if folderPath:
            self.metricsOutputFolderLineEdit.text = folderPath
            self._checkCanApply()


    def getSelectedMetrics(self):
        selectedMetrics = []

        if self.metricsVolumeCheckBox.checked:
            selectedMetrics.append("volume")

        if self.metricsDiceCheckBox.checked:
            selectedMetrics.append("dice")

        if self.metricsSurfaceAreaCheckBox.checked:
            selectedMetrics.append("surface_area")

        if self.metricsSegmentCountCheckBox.checked:
            selectedMetrics.append("segment_count")

        return selectedMetrics

    def setupMetricsGui(self) -> None:
        """
        Add post-experiment metrics controls.

        Metrics mode reads an existing experiment output folder and computes:
            - volume change
            - Dice against original
            - surface area change
            - segment count validation
        """

        self.metricsCollapsibleButton = ctk.ctkCollapsibleButton()
        self.metricsCollapsibleButton.text = "4. Metrics analysis"
        self.metricsCollapsibleButton.collapsed = True
        self.layout.addWidget(self.metricsCollapsibleButton)

        metricsLayout = qt.QVBoxLayout(self.metricsCollapsibleButton)
        metricsLayout.setContentsMargins(8, 8, 8, 8)
        metricsLayout.setSpacing(6)

        metricsLayout.addWidget(
            self.createSectionDescription(
                "Analyze saved experiment outputs. Select the original data folder and "
                "the experiment output folder containing experiment_design.csv and experiment_summary.csv."
            )
        )

        self.metricsModeCheckBox = qt.QCheckBox("Enable metrics mode")
        self.metricsModeCheckBox.checked = False
        metricsLayout.addWidget(self.metricsModeCheckBox)

        folderForm = qt.QFormLayout()
        folderForm.setContentsMargins(0, 0, 0, 0)
        folderForm.setSpacing(6)
        metricsLayout.addLayout(folderForm)

        self.metricsDataFolderLineEdit = qt.QLineEdit()
        self.metricsDataFolderLineEdit.placeholderText = (
            "Folder containing original Volume_XXX and Segmentation_XXX files"
        )
        dataFolderLayout, self.metricsDataFolderButton = self.createFolderSelectorRow(
            self.metricsDataFolderLineEdit
        )
        folderForm.addRow("Original data folder:", dataFolderLayout)

        self.metricsOutputFolderLineEdit = qt.QLineEdit()
        self.metricsOutputFolderLineEdit.placeholderText = (
            "Experiment output folder containing experiment_design.csv and experiment_summary.csv"
        )
        outputFolderLayout, self.metricsOutputFolderButton = self.createFolderSelectorRow(
            self.metricsOutputFolderLineEdit
        )
        folderForm.addRow("Experiment output folder:", outputFolderLayout)

        metricGroupBox = qt.QGroupBox("Metrics to compute")
        metricGrid = qt.QGridLayout(metricGroupBox)
        metricGrid.setContentsMargins(8, 8, 8, 8)
        metricGrid.setSpacing(6)
        metricsLayout.addWidget(metricGroupBox)

        self.metricsVolumeCheckBox = qt.QCheckBox("Volume change")
        self.metricsVolumeCheckBox.checked = True

        self.metricsDiceCheckBox = qt.QCheckBox("Dice against original")
        self.metricsDiceCheckBox.checked = True

        self.metricsSurfaceAreaCheckBox = qt.QCheckBox("Surface area change")
        self.metricsSurfaceAreaCheckBox.checked = True

        self.metricsSegmentCountCheckBox = qt.QCheckBox("Segment count validation")
        self.metricsSegmentCountCheckBox.checked = True

        metricGrid.addWidget(self.metricsVolumeCheckBox, 0, 0)
        metricGrid.addWidget(self.metricsDiceCheckBox, 0, 1)
        metricGrid.addWidget(self.metricsSurfaceAreaCheckBox, 1, 0)
        metricGrid.addWidget(self.metricsSegmentCountCheckBox, 1, 1)

        optionsGroupBox = qt.QGroupBox("Options")
        optionsGrid = qt.QGridLayout(optionsGroupBox)
        optionsGrid.setContentsMargins(8, 8, 8, 8)
        optionsGrid.setSpacing(6)
        metricsLayout.addWidget(optionsGroupBox)

        self.metricsGeneratePlotsCheckBox = qt.QCheckBox("Generate plots")
        self.metricsGeneratePlotsCheckBox.checked = True

        self.metricsRecursiveCheckBox = qt.QCheckBox("Search original data folder recursively")
        self.metricsRecursiveCheckBox.checked = True

        optionsGrid.addWidget(self.metricsGeneratePlotsCheckBox, 0, 0)
        optionsGrid.addWidget(self.metricsRecursiveCheckBox, 0, 1)

        metricsLayout.addWidget(self.createHorizontalLine())

        # Connections
        self.metricsModeCheckBox.connect("toggled(bool)", self.onMetricsModeChanged)

        self.metricsDataFolderButton.connect("clicked(bool)", self.onBrowseMetricsDataFolder)
        self.metricsOutputFolderButton.connect("clicked(bool)", self.onBrowseMetricsOutputFolder)

        self.metricsDataFolderLineEdit.connect("textChanged(QString)", self._checkCanApply)
        self.metricsOutputFolderLineEdit.connect("textChanged(QString)", self._checkCanApply)

        self.metricsVolumeCheckBox.connect("toggled(bool)", self._checkCanApply)
        self.metricsDiceCheckBox.connect("toggled(bool)", self._checkCanApply)
        self.metricsSurfaceAreaCheckBox.connect("toggled(bool)", self._checkCanApply)
        self.metricsSegmentCountCheckBox.connect("toggled(bool)", self._checkCanApply)
        self.metricsGeneratePlotsCheckBox.connect("toggled(bool)", self._checkCanApply)
        self.metricsRecursiveCheckBox.connect("toggled(bool)", self._checkCanApply)
        

    def resetProgressBars(self) -> None:
        """Reset progress bar and status text."""

        if hasattr(self, "batchProgressBar"):
            self.batchProgressBar.setValue(0)

        if hasattr(self, "progressStatusLabel"):
            self.progressStatusLabel.text = "Ready."

        if hasattr(self.ui, "statusLabel"):
            self.ui.statusLabel.text = "Ready."

        slicer.app.processEvents()


    def updateBatchProgress(self, value, text=None) -> None:
        """
        Update progress bar and status labels.
        """

        value = max(0, min(100, int(value)))

        if hasattr(self, "batchProgressBar"):
            self.batchProgressBar.setValue(value)

        if text:
            if hasattr(self, "progressStatusLabel"):
                self.progressStatusLabel.text = text

            if hasattr(self.ui, "statusLabel"):
                self.ui.statusLabel.text = text

        slicer.util.showStatusMessage(text or f"Progress: {value}%")
        slicer.app.processEvents()
        
    def onBatchModeChanged(self, checked=False) -> None:
        """
        Batch mode uses folders instead of MRML node selectors.
        """

        batchMode = self.isBatchModeEnabled()

        self.ui.inputSegmentationSelector.enabled = not batchMode
        self.ui.referenceVolumeSelector.enabled = not batchMode
        self.ui.outputSegmentationSelector.enabled = not batchMode
        self.ui.overwriteInputCheckBox.enabled = not batchMode

        if batchMode:
            self.ui.overwriteInputCheckBox.checked = False
            if self.isMetricsModeEnabled():
                self.metricsModeCheckBox.checked = False

        self.updateOutputVisibility()
        self._checkCanApply()


    def onExperimentModeChanged(self, checked=False) -> None:
        """
        Experiment mode depends on batch mode.
        """

        experimentMode = self.isExperimentModeEnabled()

        if experimentMode:
            self.batchModeCheckBox.checked = True
            if self.isMetricsModeEnabled():
                self.metricsModeCheckBox.checked = False

        self.onExperimentTypeChanged()
        self._checkCanApply()


    def onMetricsModeChanged(self, checked=False) -> None:
        """
        Metrics mode is independent from smoothing and experiment execution.
        """

        metricsMode = self.isMetricsModeEnabled()

        if metricsMode:
            self.batchModeCheckBox.checked = False
            self.experimentModeCheckBox.checked = False

            self.ui.inputSegmentationSelector.enabled = False
            self.ui.referenceVolumeSelector.enabled = False
            self.ui.outputSegmentationSelector.enabled = False
            self.ui.overwriteInputCheckBox.enabled = False

        else:
            self.onBatchModeChanged()

        self.updateOutputVisibility()
        self._checkCanApply()


    def onBrowseBatchInputFolder(self, checked=False) -> None:
        folderPath = qt.QFileDialog.getExistingDirectory(
            slicer.util.mainWindow(),
            "Select batch input folder",
            self.batchInputFolderLineEdit.text,
        )

        if folderPath:
            self.batchInputFolderLineEdit.text = folderPath
            self._checkCanApply()


    def onBrowseBatchOutputFolder(self, checked=False) -> None:
        folderPath = qt.QFileDialog.getExistingDirectory(
            slicer.util.mainWindow(),
            "Select batch output folder",
            self.batchOutputFolderLineEdit.text,
        )

        if folderPath:
            self.batchOutputFolderLineEdit.text = folderPath
            self._checkCanApply()


    def isBatchModeEnabled(self) -> bool:
        return hasattr(self, "batchModeCheckBox") and self.batchModeCheckBox.checked
        
    def isExperimentModeEnabled(self) -> bool:
        return (
            hasattr(self, "experimentModeCheckBox")
            and self.experimentModeCheckBox.checked
        )


    def onExperimentTypeChanged(self, *args) -> None:
        if not hasattr(self, "experimentTypeComboBox"):
            return

        isMonteCarlo = self.experimentTypeComboBox.currentText == "Monte Carlo"

        self.monteCarloRunsSpinBox.enabled = isMonteCarlo
        self.randomSeedSpinBox.enabled = isMonteCarlo

        if isMonteCarlo:
            self.experimentInfoLabel.text = (
                "Monte Carlo mode: enter min,max ranges for each parameter."
            )

            self.medianExperimentLineEdit.text = "1,5"
            self.openingExperimentLineEdit.text = "1,5"
            self.closingExperimentLineEdit.text = "1,5"
            self.gaussianExperimentLineEdit.text = "0.5,2.0"
            self.jointTaubinExperimentLineEdit.text = "0.2,0.8"

        else:
            self.experimentInfoLabel.text = (
                "Full factorial mode: enter comma-separated parameter values."
            )

            self.medianExperimentLineEdit.text = "1,2,3,5"
            self.openingExperimentLineEdit.text = "1,2,3,5"
            self.closingExperimentLineEdit.text = "1,2,3,5"
            self.gaussianExperimentLineEdit.text = "0.5,1.0,1.5,2.0"
            self.jointTaubinExperimentLineEdit.text = "0.2,0.4,0.6,0.8"

        self._checkCanApply()


    def parseFloatList(self, text):
        values = [
            float(value.strip())
            for value in text.split(",")
            if value.strip()
        ]

        if len(values) == 0:
            raise ValueError("At least one numeric value is required.")

        return values


    def parseFloatRange(self, text):
        values = self.parseFloatList(text)

        if len(values) != 2:
            raise ValueError("Range must contain exactly two values: min,max")

        if values[0] >= values[1]:
            raise ValueError("Range minimum must be smaller than range maximum.")

        return {
            "min": values[0],
            "max": values[1],
        }
    def cleanup(self) -> None:
        self.removeObservers()

    def enter(self) -> None:
        self.initializeParameterNode()

    def exit(self) -> None:
        if self._parameterNode:
            if self._parameterNodeGuiTag:
                self._parameterNode.disconnectGui(self._parameterNodeGuiTag)
                self._parameterNodeGuiTag = None
            self.removeObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self._checkCanApply)

    def setupGuiDefaults(self) -> None:
        """Initialize GUI defaults."""

        self.ui.scopeComboBox.clear()
        self.ui.scopeComboBox.addItem("Visible segments", "VISIBLE_SEGMENTS")
        self.ui.scopeComboBox.addItem("All segments", "ALL_SEGMENTS")

        # Default: only Joint Taubin enabled.
        # It is generally safer for multi-segment smoothing.
        self.ui.medianCheckBox.checked = False
        self.ui.openingCheckBox.checked = False
        self.ui.closingCheckBox.checked = False
        self.ui.gaussianCheckBox.checked = False
        self.ui.jointTaubinCheckBox.checked = True

        self.ui.medianKernelSizeSliderWidget.value = 3.0
        self.ui.openingKernelSizeSliderWidget.value = 3.0
        self.ui.closingKernelSizeSliderWidget.value = 3.0
        self.ui.gaussianStdSliderWidget.value = 1.0
        self.ui.jointTaubinSliderWidget.value = 0.5

        self.ui.overwriteInputCheckBox.checked = False

        if hasattr(self.ui, "statusLabel"):
            self.ui.statusLabel.text = "Ready."

        self.updateTabsFromCheckboxes()
        self.updateOutputVisibility()

    def setupConnections(self) -> None:
        """Connect GUI events."""

        self.ui.applyButton.connect("clicked(bool)", self.onApplyButton)

        self.ui.inputSegmentationSelector.connect(
            "currentNodeChanged(vtkMRMLNode*)", self._checkCanApply
        )
        self.ui.referenceVolumeSelector.connect(
            "currentNodeChanged(vtkMRMLNode*)", self._checkCanApply
        )
        self.ui.outputSegmentationSelector.connect(
            "currentNodeChanged(vtkMRMLNode*)", self._checkCanApply
        )

        self.ui.scopeComboBox.connect("currentIndexChanged(int)", self._checkCanApply)

        self.ui.medianCheckBox.connect("toggled(bool)", self.onMethodSelectionChanged)
        self.ui.openingCheckBox.connect("toggled(bool)", self.onMethodSelectionChanged)
        self.ui.closingCheckBox.connect("toggled(bool)", self.onMethodSelectionChanged)
        self.ui.gaussianCheckBox.connect("toggled(bool)", self.onMethodSelectionChanged)
        self.ui.jointTaubinCheckBox.connect("toggled(bool)", self.onMethodSelectionChanged)

        self.ui.overwriteInputCheckBox.connect("toggled(bool)", self.updateOutputVisibility)
        self.ui.overwriteInputCheckBox.connect("toggled(bool)", self._checkCanApply)

    def onSceneStartClose(self, caller, event) -> None:
        self.setParameterNode(None)

    def onSceneEndClose(self, caller, event) -> None:
        if self.parent.isEntered:
            self.initializeParameterNode()

    def initializeParameterNode(self) -> None:
        """Ensure parameter node exists and select reasonable defaults."""

        self.setParameterNode(self.logic.getParameterNode())

        if not self._parameterNode:
            return

        if not self._parameterNode.inputSegmentation:
            firstSegmentationNode = slicer.mrmlScene.GetFirstNodeByClass(
                "vtkMRMLSegmentationNode"
            )
            if firstSegmentationNode:
                self._parameterNode.inputSegmentation = firstSegmentationNode

        if not self._parameterNode.referenceVolume:
            firstVolumeNode = slicer.mrmlScene.GetFirstNodeByClass(
                "vtkMRMLScalarVolumeNode"
            )
            if firstVolumeNode:
                self._parameterNode.referenceVolume = firstVolumeNode

    def setParameterNode(self, inputParameterNode: SmoothingParameterNode | None) -> None:
        """Set and observe the parameter node."""

        if self._parameterNode:
            if self._parameterNodeGuiTag:
                self._parameterNode.disconnectGui(self._parameterNodeGuiTag)
                self._parameterNodeGuiTag = None
            self.removeObserver(
                self._parameterNode, vtk.vtkCommand.ModifiedEvent, self._checkCanApply
            )

        self._parameterNode = inputParameterNode

        if self._parameterNode:
            self._parameterNodeGuiTag = self._parameterNode.connectGui(self.ui)
            self.addObserver(
                self._parameterNode, vtk.vtkCommand.ModifiedEvent, self._checkCanApply
            )

        self._checkCanApply()

    def currentComboData(self, comboBox):
        return comboBox.itemData(comboBox.currentIndex)

    def onMethodSelectionChanged(self, checked=False) -> None:
        """Update tab availability and Apply button state when smoothing methods change."""

        self.updateTabsFromCheckboxes()
        self.updateOutputVisibility()
        self._checkCanApply()

    def updateTabsFromCheckboxes(self) -> None:
        """Enable only the parameter tabs corresponding to selected methods."""

        tabWidget = self.ui.parametersTabWidget

        methodTabs = [
            (self.ui.medianTab, self.ui.medianCheckBox.checked),
            (self.ui.openingTab, self.ui.openingCheckBox.checked),
            (self.ui.closingTab, self.ui.closingCheckBox.checked),
            (self.ui.gaussianTab, self.ui.gaussianCheckBox.checked),
            (self.ui.jointTaubinTab, self.ui.jointTaubinCheckBox.checked),
        ]

        firstEnabledIndex = -1

        for tab, enabled in methodTabs:
            index = tabWidget.indexOf(tab)
            if index >= 0:
                tabWidget.setTabEnabled(index, enabled)
                if enabled and firstEnabledIndex < 0:
                    firstEnabledIndex = index

        if firstEnabledIndex >= 0 and not tabWidget.isTabEnabled(tabWidget.currentIndex):
            tabWidget.setCurrentIndex(firstEnabledIndex)

    def updateOutputVisibility(self, *args) -> None:
        """
        Hide output selector only when overwrite is enabled and only one method is selected.

        In batch mode, the output segmentation selector is not used because outputs
        are saved directly to the selected output folder.
        """

        if self.isBatchModeEnabled():
            showOutputSelector = False
        else:
            overwrite = self.ui.overwriteInputCheckBox.checked
            numberOfMethods = len(self.getSelectedSmoothingSteps())
            showOutputSelector = not overwrite or numberOfMethods > 1

        if hasattr(self.ui, "outputSegmentationLabel"):
            self.ui.outputSegmentationLabel.visible = showOutputSelector

        self.ui.outputSegmentationSelector.visible = showOutputSelector

    def getSelectedSmoothingSteps(self):
        """
        Build the smoothing pipeline from selected checkboxes.

        The current fixed order is:
        Median -> Opening -> Closing -> Gaussian -> Joint Taubin.
        """

        steps = []

        if self.ui.medianCheckBox.checked:
            steps.append(
                {
                    "method": "MEDIAN",
                    "name": "Median",
                    "kernelSizeMm": self.ui.medianKernelSizeSliderWidget.value,
                }
            )

        if self.ui.openingCheckBox.checked:
            steps.append(
                {
                    "method": "MORPHOLOGICAL_OPENING",
                    "name": "Opening",
                    "kernelSizeMm": self.ui.openingKernelSizeSliderWidget.value,
                }
            )

        if self.ui.closingCheckBox.checked:
            steps.append(
                {
                    "method": "MORPHOLOGICAL_CLOSING",
                    "name": "Closing",
                    "kernelSizeMm": self.ui.closingKernelSizeSliderWidget.value,
                }
            )

        if self.ui.gaussianCheckBox.checked:
            steps.append(
                {
                    "method": "GAUSSIAN",
                    "name": "Gaussian",
                    "gaussianStandardDeviationMm": self.ui.gaussianStdSliderWidget.value,
                }
            )

        if self.ui.jointTaubinCheckBox.checked:
            steps.append(
                {
                    "method": "JOINT_TAUBIN",
                    "name": "Joint Taubin",
                    "jointTaubinSmoothingFactor": self.ui.jointTaubinSliderWidget.value,
                }
            )

        return steps
    def anySmoothingMethodSelected(self) -> bool:
        """Return True if at least one smoothing method is selected."""

        return (
            self.ui.medianCheckBox.checked
            or self.ui.openingCheckBox.checked
            or self.ui.closingCheckBox.checked
            or self.ui.gaussianCheckBox.checked
            or self.ui.jointTaubinCheckBox.checked
        )
    def _checkCanApply(self, caller=None, event=None) -> None:
        smoothingSteps = self.getSelectedSmoothingSteps()
        hasMethod = len(smoothingSteps) > 0

        if self.isMetricsModeEnabled():
            dataFolder = self.metricsDataFolderLineEdit.text.strip()
            outputFolder = self.metricsOutputFolderLineEdit.text.strip()

            designCsvPath = os.path.join(outputFolder, "experiment_design.csv")
            summaryCsvPath = os.path.join(outputFolder, "experiment_summary.csv")

            hasMetric = len(self.getSelectedMetrics()) > 0

            canApply = (
                os.path.isdir(dataFolder)
                and os.path.isdir(outputFolder)
                and os.path.exists(designCsvPath)
                and os.path.exists(summaryCsvPath)
                and hasMetric
            )

            self.ui.applyButton.enabled = canApply

            if canApply:
                self.ui.applyButton.toolTip = _(
                    "Analyze experiment outputs and compute selected metrics."
                )
                if hasattr(self.ui, "statusLabel"):
                    self.ui.statusLabel.text = "Ready to analyze experiment metrics."
            else:
                self.ui.applyButton.toolTip = _(
                    "Select valid data/output folders and at least one metric."
                )
                if hasattr(self.ui, "statusLabel"):
                    self.ui.statusLabel.text = (
                        "Select original data folder, experiment output folder, and metrics."
                    )

            return

        if self.isBatchModeEnabled():
            inputFolder = self.batchInputFolderLineEdit.text.strip()
            outputFolder = self.batchOutputFolderLineEdit.text.strip()

            canApply = (
                hasMethod
                and os.path.isdir(inputFolder)
                and len(outputFolder) > 0
            )

            if canApply and self.isExperimentModeEnabled():
                try:
                    if self.experimentTypeComboBox.currentText == "Full factorial":
                        self.getFullFactorialExperimentDefinitionFromGui()
                    else:
                        self.getMonteCarloExperimentDefinitionFromGui()
                except Exception:
                    canApply = False

            self.ui.applyButton.enabled = canApply

            if canApply:
                if self.isExperimentModeEnabled():
                    self.ui.applyButton.toolTip = _(
                        "Run smoothing experiment over all matched volume/segmentation pairs."
                    )
                    if hasattr(self.ui, "statusLabel"):
                        self.ui.statusLabel.text = "Ready to run smoothing experiment."
                else:
                    self.ui.applyButton.toolTip = _(
                        "Batch process all matched volume/segmentation pairs in the selected folder."
                    )
                    if hasattr(self.ui, "statusLabel"):
                        self.ui.statusLabel.text = "Ready to batch process segmentations."
            else:
                if self.isExperimentModeEnabled():
                    self.ui.applyButton.toolTip = _(
                        "Select valid batch folders, smoothing methods, and experiment values."
                    )
                    if hasattr(self.ui, "statusLabel"):
                        self.ui.statusLabel.text = (
                            "Select folders, smoothing methods, and valid experiment values."
                        )
                else:
                    self.ui.applyButton.toolTip = _(
                        "Select a valid input folder, output folder, and at least one smoothing method."
                    )
                    if hasattr(self.ui, "statusLabel"):
                        self.ui.statusLabel.text = (
                            "Select batch input folder, output folder, and smoothing methods."
                        )

            return

        inputSegmentation = self.ui.inputSegmentationSelector.currentNode()
        referenceVolume = self.ui.referenceVolumeSelector.currentNode()
        overwriteInput = self.ui.overwriteInputCheckBox.checked
        outputSegmentation = self.ui.outputSegmentationSelector.currentNode()

        multipleMethods = len(smoothingSteps) > 1

        canApply = (
            inputSegmentation is not None
            and referenceVolume is not None
            and hasMethod
        )

        if overwriteInput and multipleMethods:
            canApply = False

        if not overwriteInput:
            canApply = canApply and outputSegmentation is not None

        self.ui.applyButton.enabled = canApply

        if canApply:
            self.ui.applyButton.toolTip = _(
                "Apply each selected smoothing method independently to the original segmentation."
            )
            if hasattr(self.ui, "statusLabel"):
                self.ui.statusLabel.text = "Ready to apply smoothing."
        else:
            if overwriteInput and multipleMethods:
                self.ui.applyButton.toolTip = _(
                    "Overwrite input is only allowed when one smoothing method is selected."
                )
                if hasattr(self.ui, "statusLabel"):
                    self.ui.statusLabel.text = (
                        "Disable overwrite or select only one smoothing method."
                    )
            else:
                self.ui.applyButton.toolTip = _(
                    "Select input segmentation, reference volume, output segmentation, and at least one smoothing method."
                )
                if hasattr(self.ui, "statusLabel"):
                    self.ui.statusLabel.text = (
                        "Select the required inputs and smoothing methods."
                    )
    def getFullFactorialExperimentDefinitionFromGui(self):
        """
        Build a full factorial experiment definition from the GUI.

        Only checked smoothing methods are included.
        """

        methods = {}

        if self.ui.medianCheckBox.checked:
            methods["MEDIAN"] = {
                "name": "Median",
                "kernelSizeMm": self.parseFloatList(
                    self.medianExperimentLineEdit.text
                ),
            }

        if self.ui.openingCheckBox.checked:
            methods["MORPHOLOGICAL_OPENING"] = {
                "name": "Opening",
                "kernelSizeMm": self.parseFloatList(
                    self.openingExperimentLineEdit.text
                ),
            }

        if self.ui.closingCheckBox.checked:
            methods["MORPHOLOGICAL_CLOSING"] = {
                "name": "Closing",
                "kernelSizeMm": self.parseFloatList(
                    self.closingExperimentLineEdit.text
                ),
            }

        if self.ui.gaussianCheckBox.checked:
            methods["GAUSSIAN"] = {
                "name": "Gaussian",
                "gaussianStandardDeviationMm": self.parseFloatList(
                    self.gaussianExperimentLineEdit.text
                ),
            }

        if self.ui.jointTaubinCheckBox.checked:
            methods["JOINT_TAUBIN"] = {
                "name": "Joint Taubin",
                "jointTaubinSmoothingFactor": self.parseFloatList(
                    self.jointTaubinExperimentLineEdit.text
                ),
            }

        if not methods:
            raise ValueError("At least one smoothing method must be selected.")

        return {
            "methods": methods
        }


    def getMonteCarloExperimentDefinitionFromGui(self):
        """
        Build a Monte Carlo experiment definition from the GUI.

        Only checked smoothing methods are included.
        """

        methods = {}

        if self.ui.medianCheckBox.checked:
            methods["MEDIAN"] = {
                "name": "Median",
                "kernelSizeMm": self.parseFloatRange(
                    self.medianExperimentLineEdit.text
                ),
            }

        if self.ui.openingCheckBox.checked:
            methods["MORPHOLOGICAL_OPENING"] = {
                "name": "Opening",
                "kernelSizeMm": self.parseFloatRange(
                    self.openingExperimentLineEdit.text
                ),
            }

        if self.ui.closingCheckBox.checked:
            methods["MORPHOLOGICAL_CLOSING"] = {
                "name": "Closing",
                "kernelSizeMm": self.parseFloatRange(
                    self.closingExperimentLineEdit.text
                ),
            }

        if self.ui.gaussianCheckBox.checked:
            methods["GAUSSIAN"] = {
                "name": "Gaussian",
                "gaussianStandardDeviationMm": self.parseFloatRange(
                    self.gaussianExperimentLineEdit.text
                ),
            }

        if self.ui.jointTaubinCheckBox.checked:
            methods["JOINT_TAUBIN"] = {
                "name": "Joint Taubin",
                "jointTaubinSmoothingFactor": self.parseFloatRange(
                    self.jointTaubinExperimentLineEdit.text
                ),
            }

        if not methods:
            raise ValueError("At least one smoothing method must be selected.")

        return {
            "methods": methods
        }       
    def onApplyButton(self) -> None:
        """
        Run smoothing when the user clicks Apply.

        In normal mode:
            Apply selected smoothing methods to the selected input segmentation.

        In batch mode:
            Load all matched Volume_XXX / Segmentation_XXX pairs from a folder,
            apply selected smoothing methods independently, and save the outputs.
        """

        with slicer.util.tryWithErrorDisplay(
            _("Failed to apply smoothing."), waitCursor=True
        ):

            self.resetProgressBars()

            scope = self.currentComboData(self.ui.scopeComboBox)

            # ------------------------------------------------------------
            # Metrics mode
            # ------------------------------------------------------------
            if self.isMetricsModeEnabled():
                dataFolder = self.metricsDataFolderLineEdit.text.strip()
                outputFolder = self.metricsOutputFolderLineEdit.text.strip()
                recursive = self.metricsRecursiveCheckBox.checked
                selectedMetrics = self.getSelectedMetrics()
                generatePlots = self.metricsGeneratePlotsCheckBox.checked

                if not os.path.isdir(dataFolder):
                    raise ValueError(f"Original data folder does not exist: {dataFolder}")

                if not os.path.isdir(outputFolder):
                    raise ValueError(f"Experiment output folder does not exist: {outputFolder}")

                if not selectedMetrics:
                    raise ValueError("At least one metric must be selected.")

                self.updateBatchProgress(0, "Metrics analysis started...")

                summary = self.logic.analyzeExperimentMetrics(
                    dataFolder=dataFolder,
                    experimentOutputFolder=outputFolder,
                    selectedMetrics=selectedMetrics,
                    recursive=recursive,
                    generatePlots=generatePlots,
                    progressCallback=self.updateBatchProgress,
                )

                slicer.util.infoDisplay(
                    f"Metrics analysis completed.\n\n"
                    f"Processed rows: {summary['processed_rows']}\n"
                    f"Failed rows: {summary['failed_rows']}\n\n"
                    f"Metrics CSV:\n{summary['metrics_csv']}\n\n"
                    f"Plots folder:\n{summary['plots_folder']}"
                )

                return

            smoothingSteps = self.getSelectedSmoothingSteps()

            if not smoothingSteps:
                raise ValueError("At least one smoothing method must be selected.")

            # ------------------------------------------------------------
            # Batch mode
            # ------------------------------------------------------------
            if self.isBatchModeEnabled():
                inputFolder = self.batchInputFolderLineEdit.text.strip()
                outputFolder = self.batchOutputFolderLineEdit.text.strip()
                recursive = self.batchRecursiveCheckBox.checked
                keepLoadedNodes = self.batchKeepLoadedNodesCheckBox.checked

                if not os.path.isdir(inputFolder):
                    raise ValueError(f"Batch input folder does not exist: {inputFolder}")

                if not outputFolder:
                    raise ValueError("Batch output folder is empty.")

                # ------------------------------------------------------------
                # Experiment mode
                # ------------------------------------------------------------
                if self.isExperimentModeEnabled():
                    experimentType = self.experimentTypeComboBox.currentText

                    if experimentType == "Full factorial":
                        experimentDefinition = self.getFullFactorialExperimentDefinitionFromGui()
                        experimentRuns = self.logic.generateFullFactorialExperimentRuns(
                            experimentDefinition
                        )

                    elif experimentType == "Monte Carlo":
                        experimentDefinition = self.getMonteCarloExperimentDefinitionFromGui()
                        experimentRuns = self.logic.generateMonteCarloExperimentRuns(
                            experimentDefinition=experimentDefinition,
                            numberOfRuns=self.monteCarloRunsSpinBox.value,
                            randomSeed=self.randomSeedSpinBox.value,
                        )

                    else:
                        raise ValueError(f"Unsupported experiment type: {experimentType}")

                    self.updateBatchProgress(0, "Smoothing experiment started...")

                    summary = self.logic.runSmoothingExperiment(
                        inputFolder=inputFolder,
                        outputFolder=outputFolder,
                        experimentRuns=experimentRuns,
                        scope=scope,
                        recursive=recursive,
                        keepLoadedNodes=keepLoadedNodes,
                        progressCallback=self.updateBatchProgress,
                    )

                    slicer.util.infoDisplay(
                        f"Smoothing experiment completed.\n\n"
                        f"Found pairs: {summary['found_pairs']}\n"
                        f"Experiment runs: {summary['experiment_runs']}\n"
                        f"Successful outputs: {summary['successful_outputs']}\n"
                        f"Failed outputs: {summary['failed_outputs']}\n\n"
                        f"Design CSV:\n{summary['design_csv']}\n\n"
                        f"Summary CSV:\n{summary['summary_csv']}\n\n"
                        f"Output folder:\n{outputFolder}"
                    )

                    return

                # ------------------------------------------------------------
                # Regular batch mode
                # ------------------------------------------------------------
                self.updateBatchProgress(0, "Batch smoothing started...")

                summary = self.logic.batchSmoothSegmentations(
                    inputFolder=inputFolder,
                    outputFolder=outputFolder,
                    steps=smoothingSteps,
                    scope=scope,
                    recursive=recursive,
                    keepLoadedNodes=keepLoadedNodes,
                    progressCallback=self.updateBatchProgress,
                )

                self.updateBatchProgress(
                    100,
                    (
                        f"Batch completed. Processed {summary['processed_pairs']} pair(s), "
                        f"saved {summary['saved_outputs']} output file(s), "
                        f"failed {len(summary['failed_pairs'])} pair(s)."
                    )
                )

                failureText = ""

                if len(summary["failed_pairs"]) > 0:
                    failureLines = []

                    for failedPair in summary["failed_pairs"]:
                        failureLines.append(
                            f"- Sample {failedPair['sampleId']}: {failedPair['error']}"
                        )

                    failureText = "\n\nFailures:\n" + "\n".join(failureLines)

                slicer.util.infoDisplay(
                    f"Batch smoothing completed.\n\n"
                    f"Found pairs: {summary['found_pairs']}\n"
                    f"Processed pairs: {summary['processed_pairs']}\n"
                    f"Saved outputs: {summary['saved_outputs']}\n"
                    f"Failed pairs: {len(summary['failed_pairs'])}\n"
                    f"Output folder:\n{outputFolder}"
                    f"{failureText}"
                )

                return

            # ------------------------------------------------------------
            # Single segmentation mode
            # ------------------------------------------------------------
            inputSegmentation = self.ui.inputSegmentationSelector.currentNode()
            referenceVolume = self.ui.referenceVolumeSelector.currentNode()
            overwriteInput = self.ui.overwriteInputCheckBox.checked

            if overwriteInput and len(smoothingSteps) > 1:
                raise ValueError(
                    "Overwrite input can only be used when one smoothing method is selected. "
                    "Disable overwrite to generate one independent output segmentation per method."
                )


            if overwriteInput:
                step = smoothingSteps[0]
                methodName = step.get("name", step["method"])


                self.logic.smoothSegmentation(
                    segmentationNode=inputSegmentation,
                    referenceVolumeNode=referenceVolume,
                    method=step["method"],
                    scope=scope,
                    kernelSizeMm=step.get("kernelSizeMm", 3.0),
                    gaussianStandardDeviationMm=step.get(
                        "gaussianStandardDeviationMm", 1.0
                    ),
                    jointTaubinSmoothingFactor=step.get(
                        "jointTaubinSmoothingFactor", 0.5
                    ),
                )

                outputNodes = [inputSegmentation]

            else:
                outputNodes = self.logic.smoothSegmentationIndependently(
                    inputSegmentationNode=inputSegmentation,
                    referenceVolumeNode=referenceVolume,
                    steps=smoothingSteps,
                    scope=scope,
                    baseOutputSegmentationNode=self.ui.outputSegmentationSelector.currentNode(),
                )
            slicer.util.infoDisplay(
                f"Smoothing completed. Created {len(outputNodes)} output segmentation(s)."
            )

class SmoothingLogic(ScriptedLoadableModuleLogic):
    def __init__(self) -> None:
        ScriptedLoadableModuleLogic.__init__(self)
        self.smoothingEngine = SmoothingEngine()
        self.batchProcessor = BatchProcessor()
        self.experimentProcessor = ExperimentProcessor()
        self.metricsProcessor = MetricsProcessor()

    def getParameterNode(self):
        return SmoothingParameterNode(super().getParameterNode())

    def smoothSegmentation(self, *args, **kwargs):
        return self.smoothingEngine.smoothSegmentation(*args, **kwargs)

    def smoothSegmentationIndependently(self, *args, **kwargs):
        return self.smoothingEngine.smoothSegmentationIndependently(*args, **kwargs)

    def smoothSegmentationPipeline(self, *args, **kwargs):
        return self.smoothingEngine.smoothSegmentationPipeline(*args, **kwargs)

    def batchSmoothSegmentations(self, *args, **kwargs):
        return self.batchProcessor.batchSmoothSegmentations(*args, **kwargs)

    def generateFullFactorialExperimentRuns(self, *args, **kwargs):
        return self.experimentProcessor.generateFullFactorialExperimentRuns(*args, **kwargs)

    def generateMonteCarloExperimentRuns(self, *args, **kwargs):
        return self.experimentProcessor.generateMonteCarloExperimentRuns(*args, **kwargs)

    def runSmoothingExperiment(self, *args, **kwargs):
        return self.experimentProcessor.runSmoothingExperiment(*args, **kwargs)

    def analyzeExperimentMetrics(self, *args, **kwargs):
        return self.metricsProcessor.analyzeExperimentMetrics(*args, **kwargs)


class SmoothingTest(ScriptedLoadableModuleTest):
    """
    Test Smoothing module using one random volume/segmentation pair
    from the extension Data folder.

    Expected Data folder structure:
        Smoothing/
            Smoothing.py
            Data/
                case01_volume.nrrd
                case01_segmentation.seg.nrrd

    The test searches for volume files and segmentation files with matching
    basename fragments, then randomly selects one pair.
    """

    def setUp(self):
        slicer.mrmlScene.Clear()

    def runTest(self):
        self.setUp()
        self.test_LoadRandomDataAndApplyAllSmoothingMethods()

    def test_LoadRandomDataAndApplyAllSmoothingMethods(self):
        import os
        import random
        self.delayDisplay("Testing Smoothing with random data pair")

        logic = SmoothingLogic()
        self.assertIsNotNone(logic)

        # ------------------------------------------------------------
        # 1) Locate extension data folder
        # ------------------------------------------------------------
        moduleDir = os.path.dirname(os.path.abspath(__file__))

        # Smoothing.py is inside:
        #   SMOOTHING_EXTENSION/Smoothing/Smoothing.py
        #
        # The data folder is at:
        #   SMOOTHING_EXTENSION/data
        extensionRootDir = os.path.abspath(os.path.join(moduleDir, os.pardir))
        dataDir = os.path.join(extensionRootDir, "data")

        self.assertTrue(
            os.path.isdir(dataDir),
            f"Data folder not found: {dataDir}"
        )

        # ------------------------------------------------------------
        # 2) Find candidate volume and segmentation files
        # ------------------------------------------------------------
        volumeExtensions = [
            ".nii",
            ".nii.gz",
            ".nrrd",
            ".nhdr",
            ".mha",
            ".mhd",
        ]

        segmentationExtensions = [
            ".seg.nrrd",
            ".seg.nhdr",
            ".seg.vtm",
            ".seg.vtk",
            ".nrrd",
            ".nii",
            ".nii.gz",
        ]

        allFiles = []
        for root, dirs, files in os.walk(dataDir):
            for fileName in files:
                allFiles.append(os.path.join(root, fileName))

        segmentationFiles = [
            f for f in allFiles
            if self._isSegmentationFile(f, segmentationExtensions)
        ]

        volumeFiles = [
            f for f in allFiles
            if self._isVolumeFile(f, volumeExtensions)
            and not self._isSegmentationFile(f, segmentationExtensions)
        ]

        self.assertGreater(
            len(volumeFiles),
            0,
            f"No volume files found in: {dataDir}"
        )

        self.assertGreater(
            len(segmentationFiles),
            0,
            f"No segmentation files found in: {dataDir}"
        )

        # ------------------------------------------------------------
        # 3) Match volume and segmentation files by normalized basename
        # ------------------------------------------------------------
        pairs = FileUtils.findVolumeSegmentationPairs(
            folderPath=dataDir,
            recursive=True,
        )

        self.assertGreater(
            len(pairs),
            0,
            (
                "No matching volume/segmentation pairs found. "
                "Use related file names such as: "
                "'case01_volume.nrrd' and 'case01_segmentation.seg.nrrd'."
            )
        )

        selectedPair = random.choice(pairs)
        volumePath = selectedPair["volumePath"]
        segmentationPath = selectedPair["segmentationPath"]

        self.delayDisplay(f"Selected volume: {os.path.basename(volumePath)}")
        self.delayDisplay(f"Selected segmentation: {os.path.basename(segmentationPath)}")

        # ------------------------------------------------------------
        # 4) Load selected volume and segmentation
        # ------------------------------------------------------------
        referenceVolumeNode = slicer.util.loadVolume(volumePath)

        self.assertIsNotNone(
            referenceVolumeNode,
            f"Failed to load volume: {volumePath}"
        )

        segmentationNode = slicer.util.loadSegmentation(segmentationPath)

        self.assertIsNotNone(
            segmentationNode,
            f"Failed to load segmentation: {segmentationPath}"
        )

        segmentationNode.CreateDefaultDisplayNodes()

        self.assertGreater(
            segmentationNode.GetSegmentation().GetNumberOfSegments(),
            0,
            "Loaded segmentation does not contain any segments."
        )

        # ------------------------------------------------------------
        # 5) Define all smoothing algorithms with default parameters
        # ------------------------------------------------------------
        smoothingSteps = [
            {
                "method": "MEDIAN",
                "name": "Median",
                "kernelSizeMm": 3.0,
            },
            {
                "method": "MORPHOLOGICAL_OPENING",
                "name": "Opening",
                "kernelSizeMm": 3.0,
            },
            {
                "method": "MORPHOLOGICAL_CLOSING",
                "name": "Closing",
                "kernelSizeMm": 3.0,
            },
            {
                "method": "GAUSSIAN",
                "name": "Gaussian",
                "gaussianStandardDeviationMm": 1.0,
            },
            {
                "method": "JOINT_TAUBIN",
                "name": "Joint Taubin",
                "jointTaubinSmoothingFactor": 0.5,
            },
        ]

        # ------------------------------------------------------------
        # 6) Apply all algorithms independently to the original segmentation
        # ------------------------------------------------------------
        outputNodes = logic.smoothSegmentationIndependently(
            inputSegmentationNode=segmentationNode,
            referenceVolumeNode=referenceVolumeNode,
            steps=smoothingSteps,
            scope="ALL_SEGMENTS",
            baseOutputSegmentationNode=None,
        )

        self.assertEqual(
            len(outputNodes),
            len(smoothingSteps),
            "Unexpected number of output segmentations."
        )

        # ------------------------------------------------------------
        # 7) Validate outputs
        # ------------------------------------------------------------
        inputSegmentCount = segmentationNode.GetSegmentation().GetNumberOfSegments()

        for outputNode in outputNodes:
            self.assertIsNotNone(outputNode)
            self.assertIsInstance(outputNode, slicer.vtkMRMLSegmentationNode)

            outputSegmentCount = outputNode.GetSegmentation().GetNumberOfSegments()

            self.assertEqual(
                outputSegmentCount,
                inputSegmentCount,
                (
                    f"Output segmentation '{outputNode.GetName()}' has a different "
                    "number of segments than the input segmentation."
                )
            )

            outputNode.GetSegmentation().CreateRepresentation(
                slicer.vtkSegmentationConverter.GetSegmentationBinaryLabelmapRepresentationName()
            )

        self.delayDisplay(
            f"Test passed. Applied {len(smoothingSteps)} smoothing algorithms."
        )

    def _isVolumeFile(self, filePath, volumeExtensions):
        fileName = os.path.basename(filePath).lower()
        return any(fileName.endswith(ext) for ext in volumeExtensions)

    def _isSegmentationFile(self, filePath, segmentationExtensions):
        fileName = os.path.basename(filePath).lower()

        # Prefer explicit Slicer segmentation names.
        if ".seg." in fileName:
            return True

        # Also allow files clearly named as segmentation/label/mask.
        segmentationKeywords = [
            "seg",
            "segmentation",
            "label",
            "labels",
            "mask",
        ]

        hasSegmentationKeyword = any(
            keyword in fileName for keyword in segmentationKeywords
        )

        hasSupportedExtension = any(
            fileName.endswith(ext) for ext in segmentationExtensions
        )

        return hasSegmentationKeyword and hasSupportedExtension