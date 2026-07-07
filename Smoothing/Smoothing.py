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
        ScriptedLoadableModuleWidget.setup(self)

        uiWidget = slicer.util.loadUI(self.resourcePath("UI/Smoothing.ui"))
        self.layout.addWidget(uiWidget)
        self.ui = slicer.util.childWidgetVariables(uiWidget)
        uiWidget.setMRMLScene(slicer.mrmlScene)

        self.logic = SmoothingLogic()

        self.addObserver(slicer.mrmlScene, slicer.mrmlScene.StartCloseEvent, self.onSceneStartClose)
        self.addObserver(slicer.mrmlScene, slicer.mrmlScene.EndCloseEvent, self.onSceneEndClose)

        self.setupBatchGui()
        self.setupExperimentGui()
        self.setupMetricsGui()
        self.setupProgressGui()
        self.setupGuiDefaults()
        self.setupConnections()

        self.initializeParameterNode()
    
    def setupProgressGui(self) -> None:
        """
        Add progress bars for normal smoothing and batch processing.
        """

        self.progressCollapsibleButton = ctk.ctkCollapsibleButton()
        self.progressCollapsibleButton.text = "Progress"
        self.progressCollapsibleButton.collapsed = False

        # Put progress near the bottom, before the final spacer if possible.
        self.layout.insertWidget(self.layout.count() - 1, self.progressCollapsibleButton)

        progressLayout = qt.QFormLayout(self.progressCollapsibleButton)

        self.batchProgressBar = qt.QProgressBar()
        self.batchProgressBar.minimum = 0
        self.batchProgressBar.maximum = 100
        self.batchProgressBar.value = 0
        self.batchProgressBar.textVisible = True
        progressLayout.addRow("Batch:", self.batchProgressBar)
        
    def setupBatchGui(self) -> None:
        """
        Add batch-processing controls programmatically.

        This avoids having to manually edit Smoothing.ui for now.
        Batch mode processes a folder containing paired files such as:

            Volume_001.nrrd
            Segmentation_001.seg.nrrd

        and writes one output segmentation per selected smoothing method.
        """

        self.batchCollapsibleButton = ctk.ctkCollapsibleButton()
        self.batchCollapsibleButton.text = "Batch processing"
        self.batchCollapsibleButton.collapsed = True
        self.layout.addWidget(self.batchCollapsibleButton)

        batchLayout = qt.QFormLayout(self.batchCollapsibleButton)

        self.batchModeCheckBox = qt.QCheckBox()
        self.batchModeCheckBox.text = "Enable batch processing from folder"
        self.batchModeCheckBox.checked = False
        batchLayout.addRow(self.batchModeCheckBox)

        self.batchInputFolderLineEdit = qt.QLineEdit()
        self.batchInputFolderLineEdit.placeholderText = "Folder containing Volume_XXX and Segmentation_XXX files"

        self.batchInputFolderButton = qt.QPushButton("Browse...")
        inputFolderLayout = qt.QHBoxLayout()
        inputFolderLayout.addWidget(self.batchInputFolderLineEdit)
        inputFolderLayout.addWidget(self.batchInputFolderButton)
        batchLayout.addRow("Input folder:", inputFolderLayout)

        self.batchOutputFolderLineEdit = qt.QLineEdit()
        self.batchOutputFolderLineEdit.placeholderText = "Folder where smoothed segmentations will be saved"

        self.batchOutputFolderButton = qt.QPushButton("Browse...")
        outputFolderLayout = qt.QHBoxLayout()
        outputFolderLayout.addWidget(self.batchOutputFolderLineEdit)
        outputFolderLayout.addWidget(self.batchOutputFolderButton)
        batchLayout.addRow("Output folder:", outputFolderLayout)

        self.batchRecursiveCheckBox = qt.QCheckBox()
        self.batchRecursiveCheckBox.text = "Search recursively"
        self.batchRecursiveCheckBox.checked = True
        batchLayout.addRow(self.batchRecursiveCheckBox)

        self.batchKeepLoadedNodesCheckBox = qt.QCheckBox()
        self.batchKeepLoadedNodesCheckBox.text = "Keep loaded batch nodes in scene"
        self.batchKeepLoadedNodesCheckBox.checked = False
        batchLayout.addRow(self.batchKeepLoadedNodesCheckBox)

        self.batchModeCheckBox.connect("toggled(bool)", self.onBatchModeChanged)
        self.batchInputFolderButton.connect("clicked(bool)", self.onBrowseBatchInputFolder)
        self.batchOutputFolderButton.connect("clicked(bool)", self.onBrowseBatchOutputFolder)
        self.batchInputFolderLineEdit.connect("textChanged(QString)", self._checkCanApply)
        self.batchOutputFolderLineEdit.connect("textChanged(QString)", self._checkCanApply)
        self.batchRecursiveCheckBox.connect("toggled(bool)", self._checkCanApply)

    def setupExperimentGui(self) -> None:
        """
        Add experiment controls.

        Experiment mode reuses the batch input/output folders and runs
        multiple smoothing parameter configurations over all matched cases.
        """

        self.experimentCollapsibleButton = ctk.ctkCollapsibleButton()
        self.experimentCollapsibleButton.text = "Experiment / Design of Experiments"
        self.experimentCollapsibleButton.collapsed = True
        self.layout.addWidget(self.experimentCollapsibleButton)

        experimentLayout = qt.QFormLayout(self.experimentCollapsibleButton)

        self.experimentModeCheckBox = qt.QCheckBox()
        self.experimentModeCheckBox.text = "Enable experiment mode"
        self.experimentModeCheckBox.checked = False
        experimentLayout.addRow(self.experimentModeCheckBox)

        self.experimentTypeComboBox = qt.QComboBox()
        self.experimentTypeComboBox.addItem("Full factorial")
        self.experimentTypeComboBox.addItem("Monte Carlo")
        experimentLayout.addRow("Experiment type:", self.experimentTypeComboBox)

        self.monteCarloRunsSpinBox = qt.QSpinBox()
        self.monteCarloRunsSpinBox.minimum = 1
        self.monteCarloRunsSpinBox.maximum = 10000
        self.monteCarloRunsSpinBox.value = 20
        experimentLayout.addRow("Monte Carlo runs:", self.monteCarloRunsSpinBox)

        self.randomSeedSpinBox = qt.QSpinBox()
        self.randomSeedSpinBox.minimum = 0
        self.randomSeedSpinBox.maximum = 999999
        self.randomSeedSpinBox.value = 42
        experimentLayout.addRow("Random seed:", self.randomSeedSpinBox)

        self.experimentInfoLabel = qt.QLabel()
        self.experimentInfoLabel.wordWrap = True
        self.experimentInfoLabel.text = (
            "For full factorial, enter comma-separated values. "
            "For Monte Carlo, enter min,max ranges."
        )
        experimentLayout.addRow(self.experimentInfoLabel)

        self.medianExperimentLineEdit = qt.QLineEdit()
        self.medianExperimentLineEdit.text = "1,2,3,5"
        experimentLayout.addRow("Median kernel [mm]:", self.medianExperimentLineEdit)

        self.openingExperimentLineEdit = qt.QLineEdit()
        self.openingExperimentLineEdit.text = "1,2,3,5"
        experimentLayout.addRow("Opening kernel [mm]:", self.openingExperimentLineEdit)

        self.closingExperimentLineEdit = qt.QLineEdit()
        self.closingExperimentLineEdit.text = "1,2,3,5"
        experimentLayout.addRow("Closing kernel [mm]:", self.closingExperimentLineEdit)

        self.gaussianExperimentLineEdit = qt.QLineEdit()
        self.gaussianExperimentLineEdit.text = "0.5,1.0,1.5,2.0"
        experimentLayout.addRow("Gaussian sigma [mm]:", self.gaussianExperimentLineEdit)

        self.jointTaubinExperimentLineEdit = qt.QLineEdit()
        self.jointTaubinExperimentLineEdit.text = "0.2,0.4,0.6,0.8"
        experimentLayout.addRow("Joint Taubin factor:", self.jointTaubinExperimentLineEdit)

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
        Add controls for post-experiment metrics analysis.

        Metrics mode reads:
            - original data folder
            - experiment output folder
            - experiment_design.csv
            - experiment_summary.csv

        Then it computes selected metrics and saves:
            - experiment_metrics.csv
            - plots/*.png
        """

        self.metricsCollapsibleButton = ctk.ctkCollapsibleButton()
        self.metricsCollapsibleButton.text = "Metrics analysis"
        self.metricsCollapsibleButton.collapsed = True
        self.layout.addWidget(self.metricsCollapsibleButton)

        metricsLayout = qt.QFormLayout(self.metricsCollapsibleButton)

        self.metricsModeCheckBox = qt.QCheckBox()
        self.metricsModeCheckBox.text = "Enable metrics mode"
        self.metricsModeCheckBox.checked = False
        metricsLayout.addRow(self.metricsModeCheckBox)

        self.metricsDataFolderLineEdit = qt.QLineEdit()
        self.metricsDataFolderLineEdit.placeholderText = (
            "Folder containing original Volume_XXX and Segmentation_XXX files"
        )

        self.metricsDataFolderButton = qt.QPushButton("Browse...")
        dataFolderLayout = qt.QHBoxLayout()
        dataFolderLayout.addWidget(self.metricsDataFolderLineEdit)
        dataFolderLayout.addWidget(self.metricsDataFolderButton)
        metricsLayout.addRow("Original data folder:", dataFolderLayout)

        self.metricsOutputFolderLineEdit = qt.QLineEdit()
        self.metricsOutputFolderLineEdit.placeholderText = (
            "Experiment output folder containing experiment_design.csv and experiment_summary.csv"
        )

        self.metricsOutputFolderButton = qt.QPushButton("Browse...")
        outputFolderLayout = qt.QHBoxLayout()
        outputFolderLayout.addWidget(self.metricsOutputFolderLineEdit)
        outputFolderLayout.addWidget(self.metricsOutputFolderButton)
        metricsLayout.addRow("Experiment output folder:", outputFolderLayout)

        self.metricsVolumeCheckBox = qt.QCheckBox()
        self.metricsVolumeCheckBox.text = "Volume change"
        self.metricsVolumeCheckBox.checked = True
        metricsLayout.addRow(self.metricsVolumeCheckBox)

        self.metricsDiceCheckBox = qt.QCheckBox()
        self.metricsDiceCheckBox.text = "Dice against original"
        self.metricsDiceCheckBox.checked = True
        metricsLayout.addRow(self.metricsDiceCheckBox)

        self.metricsSurfaceAreaCheckBox = qt.QCheckBox()
        self.metricsSurfaceAreaCheckBox.text = "Surface area change"
        self.metricsSurfaceAreaCheckBox.checked = True
        metricsLayout.addRow(self.metricsSurfaceAreaCheckBox)

        self.metricsSegmentCountCheckBox = qt.QCheckBox()
        self.metricsSegmentCountCheckBox.text = "Segment count validation"
        self.metricsSegmentCountCheckBox.checked = True
        metricsLayout.addRow(self.metricsSegmentCountCheckBox)

        self.metricsGeneratePlotsCheckBox = qt.QCheckBox()
        self.metricsGeneratePlotsCheckBox.text = "Generate plots"
        self.metricsGeneratePlotsCheckBox.checked = True
        metricsLayout.addRow(self.metricsGeneratePlotsCheckBox)

        self.metricsRecursiveCheckBox = qt.QCheckBox()
        self.metricsRecursiveCheckBox.text = "Search original data folder recursively"
        self.metricsRecursiveCheckBox.checked = True
        metricsLayout.addRow(self.metricsRecursiveCheckBox)

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

        

    def resetProgressBars(self) -> None:
        """Reset progress bars."""

        if hasattr(self, "batchProgressBar"):
            self.batchProgressBar.setValue(0)
            self.batchProgressBar.repaint()

        slicer.app.processEvents()


    def updateBatchProgress(self, value, text=None) -> None:
        """Update batch progress bar."""

        value = max(0, min(100, int(value)))

        if hasattr(self, "batchProgressBar"):
            self.batchProgressBar.setValue(value)
            self.batchProgressBar.repaint()

        if text and hasattr(self.ui, "statusLabel"):
            self.ui.statusLabel.text = text
            self.ui.statusLabel.repaint()

        slicer.util.showStatusMessage(text or f"Batch progress: {value}%")
        slicer.app.processEvents()
        
    def onBatchModeChanged(self, checked=False) -> None:
        """
        Update GUI state when batch mode is enabled or disabled.

        In batch mode, the input/output MRML selectors are not required.
        The selected folder is used instead.
        """

        batchMode = self.batchModeCheckBox.checked

        self.ui.inputSegmentationSelector.enabled = not batchMode
        self.ui.referenceVolumeSelector.enabled = not batchMode
        self.ui.outputSegmentationSelector.enabled = not batchMode
        self.ui.overwriteInputCheckBox.enabled = not batchMode

        if batchMode:
            self.ui.overwriteInputCheckBox.checked = False

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

    def onExperimentModeChanged(self, checked=False) -> None:
        """
        Experiment mode requires batch mode because it processes an input folder
        and writes many outputs to an output folder.
        """

        experimentMode = self.isExperimentModeEnabled()

        if experimentMode:
            self.batchModeCheckBox.checked = True

        self.onExperimentTypeChanged()
        self._checkCanApply()


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
    """Computation logic for segmentation smoothing."""

    def __init__(self) -> None:
        ScriptedLoadableModuleLogic.__init__(self)

    def loadSegmentationNodeRobust(self, segmentationPath, referenceVolumeNode=None):
        """
        Load a segmentation file robustly.

        First tries slicer.util.loadSegmentation().
        If that fails, tries to load the file as a labelmap volume and convert it
        to a segmentation node.

        This is needed because .nrrd files may be either:
            - true Slicer segmentation files: .seg.nrrd
            - labelmap volumes: .nrrd
        """

        logging.info(f"[BATCH LOAD] Trying to load segmentation: {segmentationPath}")

        try:
            segmentationNode = slicer.util.loadSegmentation(segmentationPath)
        except Exception as exc:
            logging.warning(
                f"[BATCH LOAD] loadSegmentation raised an exception: {str(exc)}"
            )
            segmentationNode = None

        if segmentationNode is not None:
            logging.info(
                f"[BATCH LOAD] Loaded as segmentation node: {segmentationNode.GetName()}"
            )
            return segmentationNode

        logging.warning(
            f"[BATCH LOAD] loadSegmentation failed. Trying as labelmap volume: {segmentationPath}"
        )

        try:
            labelmapNode = slicer.util.loadLabelVolume(segmentationPath)
        except Exception as exc:
            logging.warning(
                f"[BATCH LOAD] loadLabelVolume raised an exception: {str(exc)}"
            )
            labelmapNode = None

        if labelmapNode is None:
            raise RuntimeError(
                f"Failed to load segmentation as .seg.nrrd or labelmap: {segmentationPath}"
            )

        segmentationNode = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLSegmentationNode",
            os.path.splitext(os.path.basename(segmentationPath))[0] + "_Segmentation",
        )

        segmentationNode.CreateDefaultDisplayNodes()

        if referenceVolumeNode is not None:
            segmentationNode.SetReferenceImageGeometryParameterFromVolumeNode(referenceVolumeNode)

        slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
            labelmapNode,
            segmentationNode,
        )

        slicer.mrmlScene.RemoveNode(labelmapNode)

        if segmentationNode.GetSegmentation().GetNumberOfSegments() == 0:
            raise RuntimeError(
                f"Loaded labelmap but no segments were imported: {segmentationPath}"
            )

        logging.info(
            f"[BATCH LOAD] Loaded labelmap and converted to segmentation: "
            f"{segmentationNode.GetName()} with "
            f"{segmentationNode.GetSegmentation().GetNumberOfSegments()} segment(s)"
        )

        return segmentationNode

    def readCsvRows(self, csvPath):
        if not os.path.exists(csvPath):
            raise ValueError(f"CSV file does not exist: {csvPath}")

        with open(csvPath, newline="") as csvFile:
            return list(csv.DictReader(csvFile))
        
    def generateFullFactorialExperimentRuns(self, experimentDefinition):
        """
        Generate full factorial smoothing experiment runs.

        Each method is treated independently. For each selected method, all
        combinations of its parameter values are generated.
        """

        runs = []
        runIndex = 1

        for method, methodConfig in experimentDefinition["methods"].items():
            methodName = methodConfig.get("name", method)

            parameterGrid = {
                key: value
                for key, value in methodConfig.items()
                if key != "name"
            }

            if not parameterGrid:
                raise ValueError(f"No parameters defined for method: {method}")

            parameterNames = list(parameterGrid.keys())
            parameterValues = [parameterGrid[name] for name in parameterNames]

            for values in parameterValues:
                if not isinstance(values, list):
                    raise ValueError(
                        f"Full factorial parameter values must be lists. "
                        f"Invalid parameter in method {method}."
                    )

                if len(values) == 0:
                    raise ValueError(
                        f"Empty parameter list in method {method}."
                    )

            for combination in itertools.product(*parameterValues):
                run = {
                    "runId": f"run_{runIndex:04d}",
                    "method": method,
                    "name": methodName,
                }

                for parameterName, parameterValue in zip(parameterNames, combination):
                    run[parameterName] = parameterValue

                runs.append(run)
                runIndex += 1

        return runs
    def generateMonteCarloExperimentRuns(
        self,
        experimentDefinition,
        numberOfRuns=20,
        randomSeed=42,
    ):
        """
        Generate Monte Carlo smoothing experiment runs.

        experimentDefinition example:
            {
                "methods": {
                    "MEDIAN": {
                        "name": "Median",
                        "kernelSizeMm": {"min": 1.0, "max": 7.0}
                    },
                    "GAUSSIAN": {
                        "name": "Gaussian",
                        "gaussianStandardDeviationMm": {"min": 0.2, "max": 3.0}
                    },
                    "JOINT_TAUBIN": {
                        "name": "Joint Taubin",
                        "jointTaubinSmoothingFactor": {"min": 0.1, "max": 1.0}
                    }
                }
            }
        """

        rng = random.Random(randomSeed)
        runs = []

        methodItems = list(experimentDefinition["methods"].items())

        for runIndex in range(1, numberOfRuns + 1):
            method, methodConfig = rng.choice(methodItems)
            methodName = methodConfig.get("name", method)

            run = {
                "runId": f"run_{runIndex:04d}",
                "method": method,
                "name": methodName,
            }

            for parameterName, parameterRange in methodConfig.items():
                if parameterName == "name":
                    continue

                minValue = float(parameterRange["min"])
                maxValue = float(parameterRange["max"])

                run[parameterName] = rng.uniform(minValue, maxValue)

            runs.append(run)

        return runs
    
    def saveExperimentDesignCsv(self, experimentRuns, outputFolder):
        """Save experiment design table as CSV."""

        os.makedirs(outputFolder, exist_ok=True)

        outputPath = os.path.join(outputFolder, "experiment_design.csv")

        fieldNames = [
            "runId",
            "method",
            "name",
            "kernelSizeMm",
            "gaussianStandardDeviationMm",
            "jointTaubinSmoothingFactor",
        ]

        with open(outputPath, "w", newline="") as csvFile:
            writer = csv.DictWriter(csvFile, fieldnames=fieldNames)
            writer.writeheader()

            for run in experimentRuns:
                writer.writerow({
                    fieldName: run.get(fieldName, "")
                    for fieldName in fieldNames
                })

        return outputPath
    def getParameterNode(self):
        return SmoothingParameterNode(super().getParameterNode())

    def copySegmentationContent(self, inputSegmentationNode, outputSegmentationNode, outputName=None):
        """
        Copy only segmentation content and force an independent display node.

        This avoids shared visibility/display behavior between segmentation nodes.
        """

        if inputSegmentationNode is None or outputSegmentationNode is None:
            raise ValueError("Input or output segmentation node is invalid.")

        if outputName:
            outputSegmentationNode.SetName(outputName)

        # ------------------------------------------------------------------
        # 1) Remove any previous display node references from the output node
        # ------------------------------------------------------------------
        outputSegmentationNode.RemoveAllDisplayNodeIDs()

        # ------------------------------------------------------------------
        # 2) Clear old segmentation content
        # ------------------------------------------------------------------
        outputSegmentationNode.GetSegmentation().RemoveAllSegments()

        # ------------------------------------------------------------------
        # 3) Deep-copy only the internal vtkSegmentation content
        # ------------------------------------------------------------------
        outputSegmentationNode.GetSegmentation().DeepCopy(
            inputSegmentationNode.GetSegmentation()
        )

        # ------------------------------------------------------------------
        # 4) Create a completely new independent display node
        # ------------------------------------------------------------------
        outputDisplayNode = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLSegmentationDisplayNode",
            outputSegmentationNode.GetName() + "_Display"
        )

        outputSegmentationNode.SetAndObserveDisplayNodeID(outputDisplayNode.GetID())

        # ------------------------------------------------------------------
        # 5) Copy transform if input segmentation has one
        # ------------------------------------------------------------------
        outputSegmentationNode.SetAndObserveTransformNodeID(
            inputSegmentationNode.GetTransformNodeID()
        )

        # ------------------------------------------------------------------
        # 6) Optional: make output visible
        # ------------------------------------------------------------------
        outputDisplayNode.SetVisibility(True)
        outputDisplayNode.SetVisibility3D(True)
        outputDisplayNode.SetVisibility2DFill(True)
        outputDisplayNode.SetVisibility2DOutline(True)
    def cloneSegmentation(self, inputSegmentationNode, outputSegmentationNode) -> None:
        """Copy input segmentation content into the output segmentation node."""

        self.copySegmentationContent(
            inputSegmentationNode=inputSegmentationNode,
            outputSegmentationNode=outputSegmentationNode,
            outputName=inputSegmentationNode.GetName() + "_smoothed",
        )

    def smoothSegmentation(
        self,
        segmentationNode,
        referenceVolumeNode,
        method="JOINT_TAUBIN",
        scope="VISIBLE_SEGMENTS",
        kernelSizeMm=3.0,
        gaussianStandardDeviationMm=1.0,
        jointTaubinSmoothingFactor=0.5,
    ) -> None:
        """Apply one Slicer Segment Editor Smoothing effect to a segmentation."""

        if segmentationNode is None:
            raise ValueError("Segmentation node is invalid.")

        if referenceVolumeNode is None:
            raise ValueError("Reference volume node is invalid.")

        startTime = time.time()
        logging.info(f"Segmentation smoothing started: {method}")

        # Make sure binary labelmap representation exists.
        segmentationNode.GetSegmentation().CreateRepresentation(
            slicer.vtkSegmentationConverter.GetSegmentationBinaryLabelmapRepresentationName()
        )

        segmentEditorWidget = slicer.qMRMLSegmentEditorWidget()
        segmentEditorWidget.setMRMLScene(slicer.mrmlScene)

        segmentEditorNode = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLSegmentEditorNode"
        )
        segmentEditorWidget.setMRMLSegmentEditorNode(segmentEditorNode)

        segmentEditorWidget.setSegmentationNode(segmentationNode)
        segmentEditorWidget.setSourceVolumeNode(referenceVolumeNode)

        originalVisibleSegmentIds = self.visibleSegmentIds(segmentationNode)

        try:
            if scope == "ALL_SEGMENTS":
                self.setAllSegmentsVisible(segmentationNode, True)

            segmentEditorWidget.setActiveEffectByName("Smoothing")
            effect = segmentEditorWidget.activeEffect()

            if effect is None:
                raise RuntimeError("Could not activate Segment Editor Smoothing effect.")

            effect.setParameter("SmoothingMethod", method)

            # Apply to all visible segments.
            # For ALL_SEGMENTS, all segments are temporarily made visible above.
            effect.setParameter("ApplyToAllVisibleSegments", "1")

            if method in [
                "MEDIAN",
                "MORPHOLOGICAL_OPENING",
                "MORPHOLOGICAL_CLOSING",
            ]:
                effect.setParameter("KernelSizeMm", str(kernelSizeMm))

            elif method == "GAUSSIAN":
                effect.setParameter(
                    "GaussianStandardDeviationMm",
                    str(gaussianStandardDeviationMm),
                )

            elif method == "JOINT_TAUBIN":
                effect.setParameter(
                    "JointTaubinSmoothingFactor",
                    str(jointTaubinSmoothingFactor),
                )

            else:
                raise ValueError(f"Unsupported smoothing method: {method}")

            effect.self().onApply()

        finally:
            if scope == "ALL_SEGMENTS":
                self.restoreVisibleSegments(segmentationNode, originalVisibleSegmentIds)

            segmentEditorWidget.setMRMLSegmentEditorNode(None)
            slicer.mrmlScene.RemoveNode(segmentEditorNode)
            segmentEditorWidget = None

        stopTime = time.time()
        logging.info(
            f"Segmentation smoothing step completed in {stopTime - startTime:.2f} seconds"
        )
    def safeNodeName(self, name: str) -> str:
        """Return a compact name fragment suitable for MRML node names."""

        return (
            name.strip()
            .replace(" ", "_")
            .replace("/", "_")
            .replace("\\", "_")
            .replace("(", "")
            .replace(")", "")
        )
    def smoothSegmentationIndependently(
        self,
        inputSegmentationNode,
        referenceVolumeNode,
        steps,
        scope="VISIBLE_SEGMENTS",
        baseOutputSegmentationNode=None,
    ):
        """
        Apply each smoothing method independently to the original segmentation.

        For each selected method:
        1. Create a fresh copy of the original segmentation.
        2. Apply exactly one smoothing method to that copy.
        3. Keep the result as an independent output segmentation.

        This intentionally does NOT apply smoothing methods sequentially.
        """

        if inputSegmentationNode is None:
            raise ValueError("Input segmentation node is invalid.")

        if referenceVolumeNode is None:
            raise ValueError("Reference volume node is invalid.")

        if not steps:
            raise ValueError("No smoothing steps were selected.")

        startTime = time.time()
        logging.info("Independent smoothing started")

        outputNodes = []

        totalSteps = len(steps)

        for stepIndex, step in enumerate(steps, start=1):
            methodName = step.get("name", step.get("method", f"Step{stepIndex}"))
            safeMethodName = self.safeNodeName(methodName)

            outputName = f"{inputSegmentationNode.GetName()}_{safeMethodName}_smoothed"

            if (
                len(steps) == 1
                and baseOutputSegmentationNode is not None
                and baseOutputSegmentationNode != inputSegmentationNode
            ):
                outputNode = baseOutputSegmentationNode
            else:
                outputNode = slicer.mrmlScene.AddNewNodeByClass(
                    "vtkMRMLSegmentationNode",
                    outputName,
                )

            self.copySegmentationContent(
                inputSegmentationNode=inputSegmentationNode,
                outputSegmentationNode=outputNode,
                outputName=outputName,
            )

            logging.info(
                f"Applying independent smoothing {stepIndex}/{len(steps)}: {methodName}"
            )

            self.smoothSegmentation(
                segmentationNode=outputNode,
                referenceVolumeNode=referenceVolumeNode,
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

            outputNodes.append(outputNode)

        stopTime = time.time()
        logging.info(
            f"Independent smoothing completed in {stopTime - startTime:.2f} seconds"
        )

        return outputNodes

    def smoothSegmentationPipeline(
        self,
        segmentationNode,
        referenceVolumeNode,
        steps,
        scope="VISIBLE_SEGMENTS",
    ) -> None:
        """Apply multiple smoothing steps sequentially."""

        if segmentationNode is None:
            raise ValueError("Segmentation node is invalid.")

        if referenceVolumeNode is None:
            raise ValueError("Reference volume node is invalid.")

        if not steps:
            raise ValueError("No smoothing steps were selected.")

        startTime = time.time()
        logging.info("Segmentation smoothing pipeline started")

        for stepIndex, step in enumerate(steps, start=1):
            logging.info(
                f"Applying smoothing step {stepIndex}/{len(steps)}: "
                f"{step.get('name', step.get('method'))}"
            )

            self.smoothSegmentation(
                segmentationNode=segmentationNode,
                referenceVolumeNode=referenceVolumeNode,
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

        stopTime = time.time()
        logging.info(
            f"Segmentation smoothing pipeline completed in {stopTime - startTime:.2f} seconds"
        )

    def visibleSegmentIds(self, segmentationNode):
        """Return list of currently visible segment IDs."""

        displayNode = segmentationNode.GetDisplayNode()
        if displayNode is None:
            segmentationNode.CreateDefaultDisplayNodes()
            displayNode = segmentationNode.GetDisplayNode()

        segmentation = segmentationNode.GetSegmentation()
        segmentIds = vtk.vtkStringArray()
        segmentation.GetSegmentIDs(segmentIds)

        visibleIds = []
        for i in range(segmentIds.GetNumberOfValues()):
            segmentId = segmentIds.GetValue(i)
            if displayNode.GetSegmentVisibility(segmentId):
                visibleIds.append(segmentId)

        return visibleIds

    def setAllSegmentsVisible(self, segmentationNode, visible=True) -> None:
        """Set visibility for all segments."""

        displayNode = segmentationNode.GetDisplayNode()
        if displayNode is None:
            segmentationNode.CreateDefaultDisplayNodes()
            displayNode = segmentationNode.GetDisplayNode()

        segmentation = segmentationNode.GetSegmentation()
        segmentIds = vtk.vtkStringArray()
        segmentation.GetSegmentIDs(segmentIds)

        for i in range(segmentIds.GetNumberOfValues()):
            segmentId = segmentIds.GetValue(i)
            displayNode.SetSegmentVisibility(segmentId, visible)

    def restoreVisibleSegments(self, segmentationNode, visibleSegmentIds) -> None:
        """Restore segment visibility after temporary all-segment processing."""

        displayNode = segmentationNode.GetDisplayNode()
        if displayNode is None:
            segmentationNode.CreateDefaultDisplayNodes()
            displayNode = segmentationNode.GetDisplayNode()

        segmentation = segmentationNode.GetSegmentation()
        segmentIds = vtk.vtkStringArray()
        segmentation.GetSegmentIDs(segmentIds)

        visibleSegmentIds = set(visibleSegmentIds)

        for i in range(segmentIds.GetNumberOfValues()):
            segmentId = segmentIds.GetValue(i)
            displayNode.SetSegmentVisibility(
                segmentId, segmentId in visibleSegmentIds
            )
    
    def isVolumeFile(self, filePath):
        fileName = os.path.basename(filePath).lower()

        volumeExtensions = [
            ".nii",
            ".nii.gz",
            ".nrrd",
            ".nhdr",
            ".mha",
            ".mhd",
        ]

        if self.isSegmentationFile(filePath):
            return False

        return any(fileName.endswith(ext) for ext in volumeExtensions)


    def isSegmentationFile(self, filePath):
        fileName = os.path.basename(filePath).lower()

        segmentationExtensions = [
            ".seg.nrrd",
            ".seg.nhdr",
            ".seg.vtm",
            ".seg.vtk",
            ".nrrd",
            ".nii",
            ".nii.gz",
            ".mha",
            ".mhd",
        ]

        if ".seg." in fileName:
            return True

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


    def sampleIdFromFileName(self, filePath):
        """
        Extract sample ID from names like:

            Volume_001.nrrd
            Volume_001.nii.gz
            Segmentation_001.seg.nrrd
            Segmentation_001.nrrd

        Returns:
            "001"

        If the file name does not match this pattern, returns None.
        """

        fileName = os.path.basename(filePath)

        match = re.search(
            r"^(Volume|Segmentation)_(.+?)(\.seg)?"
            r"(\.nii\.gz|\.nii|\.nrrd|\.nhdr|\.mha|\.mhd|\.vtk|\.vtm)$",
            fileName,
            flags=re.IGNORECASE,
        )

        if not match:
            return None

        return match.group(2)


    def collectFilesFromFolder(self, folderPath, recursive=True):
        """Collect all files from a folder."""

        allFiles = []

        if recursive:
            for root, dirs, files in os.walk(folderPath):
                for fileName in files:
                    allFiles.append(os.path.join(root, fileName))
        else:
            for fileName in os.listdir(folderPath):
                filePath = os.path.join(folderPath, fileName)
                if os.path.isfile(filePath):
                    allFiles.append(filePath)

        return allFiles


    def findVolumeSegmentationPairs(self, folderPath, recursive=True):
        """
        Find matching volume/segmentation pairs in a folder.

        Matching rule:
            Volume_XXX.ext
            Segmentation_XXX.ext
        """

        if not os.path.isdir(folderPath):
            raise ValueError(f"Folder does not exist: {folderPath}")

        allFiles = self.collectFilesFromFolder(folderPath, recursive=recursive)

        logging.info(f"[BATCH PAIRING] Folder: {folderPath}")
        logging.info(f"[BATCH PAIRING] Recursive: {recursive}")
        logging.info(f"[BATCH PAIRING] Total files found: {len(allFiles)}")

        volumeFiles = [
            filePath for filePath in allFiles
            if self.isVolumeFile(filePath)
        ]

        segmentationFiles = [
            filePath for filePath in allFiles
            if self.isSegmentationFile(filePath)
        ]

        logging.info(f"[BATCH PAIRING] Candidate volume files: {len(volumeFiles)}")
        for filePath in volumeFiles:
            logging.info(f"[BATCH PAIRING] Volume candidate: {os.path.basename(filePath)}")

        logging.info(f"[BATCH PAIRING] Candidate segmentation files: {len(segmentationFiles)}")
        for filePath in segmentationFiles:
            logging.info(f"[BATCH PAIRING] Segmentation candidate: {os.path.basename(filePath)}")

        segmentationBySampleId = {}

        for segmentationFile in segmentationFiles:
            sampleId = self.sampleIdFromFileName(segmentationFile)

            logging.info(
                f"[BATCH PAIRING] Segmentation sample ID: "
                f"{os.path.basename(segmentationFile)} -> {sampleId}"
            )

            if sampleId is not None:
                segmentationBySampleId.setdefault(sampleId, []).append(segmentationFile)

        pairs = []

        for volumeFile in volumeFiles:
            sampleId = self.sampleIdFromFileName(volumeFile)

            logging.info(
                f"[BATCH PAIRING] Volume sample ID: "
                f"{os.path.basename(volumeFile)} -> {sampleId}"
            )

            if sampleId is None:
                continue

            if sampleId in segmentationBySampleId:
                for segmentationFile in segmentationBySampleId[sampleId]:
                    pairs.append(
                        {
                            "sampleId": sampleId,
                            "volumePath": volumeFile,
                            "segmentationPath": segmentationFile,
                        }
                    )

                    logging.info(
                        f"[BATCH PAIRING] Pair found: "
                        f"{os.path.basename(volumeFile)} + "
                        f"{os.path.basename(segmentationFile)}"
                    )

        logging.info(f"[BATCH PAIRING] Total pairs found: {len(pairs)}")

        return pairs

    def batchSmoothSegmentations(
        self,
        inputFolder,
        outputFolder,
        steps,
        scope="ALL_SEGMENTS",
        recursive=True,
        keepLoadedNodes=False,
        progressCallback=None,
    ):
        """
        Batch process segmentation files from a folder.

        For each matched pair:
            Volume_XXX.ext
            Segmentation_XXX.ext

        The function:
            1. Loads the volume.
            2. Loads the segmentation.
            3. Applies each selected smoothing method independently.
            4. Saves each output segmentation as .seg.nrrd.
            5. Removes temporary nodes unless keepLoadedNodes=True.
        """

        if not os.path.isdir(inputFolder):
            raise ValueError(f"Input folder does not exist: {inputFolder}")

        if not steps:
            raise ValueError("No smoothing steps were selected.")

        os.makedirs(outputFolder, exist_ok=True)

        inputFolderAbs = os.path.abspath(inputFolder)
        outputFolderAbs = os.path.abspath(outputFolder)

        if outputFolderAbs == inputFolderAbs:
            raise ValueError(
                "The batch output folder cannot be the same as the input folder. "
                "Select a separate output folder."
            )

        if recursive and outputFolderAbs.startswith(inputFolderAbs + os.sep):
            raise ValueError(
                "The batch output folder cannot be inside the input folder when recursive search is enabled. "
                "Select a separate output folder or disable recursive search."
            )

        pairs = self.findVolumeSegmentationPairs(
            folderPath=inputFolder,
            recursive=recursive,
        )

        if not pairs:
            raise ValueError(
                "No matching volume/segmentation pairs were found. "
                "Expected names such as Volume_001.nrrd and Segmentation_001.seg.nrrd."
            )

        processedPairs = 0
        savedOutputs = 0
        failedPairs = []

        totalPairs = len(pairs)
        totalOperations = totalPairs * len(steps)
        completedOperations = 0

        if progressCallback:
            progressCallback(
                0,
                f"Batch smoothing started. Found {totalPairs} pair(s)."
            )

        logging.info(f"Batch smoothing started. Found {totalPairs} pair(s).")

        for pairIndex, pair in enumerate(pairs, start=1):
            sampleId = pair["sampleId"]
            volumePath = pair["volumePath"]
            segmentationPath = pair["segmentationPath"]

            logging.info(
                f"Processing pair {pairIndex}/{totalPairs}: "
                f"sample={sampleId}, "
                f"volume={os.path.basename(volumePath)}, "
                f"segmentation={os.path.basename(segmentationPath)}"
            )

            if progressCallback:
                progressValue = int((completedOperations / totalOperations) * 100)
                progressCallback(
                    progressValue,
                    f"Loading sample {sampleId} ({pairIndex}/{totalPairs})..."
                )

            loadedNodes = []
            outputNodes = []

            try:
                logging.info(f"[BATCH LOAD] Loading volume: {volumePath}")

                referenceVolumeNode = slicer.util.loadVolume(volumePath)

                if referenceVolumeNode is None:
                    raise RuntimeError(f"Failed to load volume: {volumePath}")

                logging.info(f"[BATCH LOAD] Loaded volume node: {referenceVolumeNode.GetName()}")

                loadedNodes.append(referenceVolumeNode)

                segmentationNode = self.loadSegmentationNodeRobust(
                    segmentationPath=segmentationPath,
                    referenceVolumeNode=referenceVolumeNode,
                )

                if segmentationNode is None:
                    raise RuntimeError(f"Failed to load segmentation: {segmentationPath}")

                segmentationNode.CreateDefaultDisplayNodes()

                logging.info(
                    f"[BATCH LOAD] Loaded segmentation node: {segmentationNode.GetName()} "
                    f"with {segmentationNode.GetSegmentation().GetNumberOfSegments()} segment(s)"
                )

                loadedNodes.append(segmentationNode)

                if segmentationNode.GetSegmentation().GetNumberOfSegments() == 0:
                    raise RuntimeError(
                        f"Segmentation has no segments: {segmentationPath}"
                    )

                for stepIndex, step in enumerate(steps, start=1):
                    methodName = step.get("name", step.get("method", f"Step{stepIndex}"))
                    safeMethodName = self.safeNodeName(methodName)

                    if progressCallback:
                        progressValue = int((completedOperations / totalOperations) * 100)
                        progressCallback(
                            progressValue,
                            (
                                f"Sample {sampleId}: applying {methodName} "
                                f"({stepIndex}/{len(steps)}), "
                                f"case {pairIndex}/{totalPairs}..."
                            )
                        )

                    outputName = (
                        f"Segmentation_{sampleId}_{safeMethodName}_smoothed"
                    )

                    outputNode = slicer.mrmlScene.AddNewNodeByClass(
                        "vtkMRMLSegmentationNode",
                        outputName,
                    )

                    self.copySegmentationContent(
                        inputSegmentationNode=segmentationNode,
                        outputSegmentationNode=outputNode,
                        outputName=outputName,
                    )

                    self.smoothSegmentation(
                        segmentationNode=outputNode,
                        referenceVolumeNode=referenceVolumeNode,
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

                    outputNodes.append(outputNode)

                    outputFileName = (
                        f"Segmentation_{sampleId}_{safeMethodName}_smoothed.seg.nrrd"
                    )

                    outputPath = os.path.join(outputFolder, outputFileName)

                    success = slicer.util.saveNode(outputNode, outputPath)

                    if not success:
                        raise RuntimeError(f"Failed to save output: {outputPath}")

                    logging.info(f"Saved: {outputPath}")
                    savedOutputs += 1

                    completedOperations += 1

                    if progressCallback:
                        progressValue = int((completedOperations / totalOperations) * 100)
                        progressCallback(
                            progressValue,
                            (
                                f"Saved {methodName} result for sample {sampleId}. "
                                f"Progress: {completedOperations}/{totalOperations}."
                            )
                        )

                processedPairs += 1

            except Exception as exc:
                logging.error(
                    f"Failed to process sample {sampleId}: {str(exc)}"
                )

                failedPairs.append(
                    {
                        "sampleId": sampleId,
                        "volumePath": volumePath,
                        "segmentationPath": segmentationPath,
                        "error": str(exc),
                    }
                )

                # Count failed sample as completed operations to avoid a stuck progress bar.
                completedOperations += len(steps)

                if completedOperations > totalOperations:
                    completedOperations = totalOperations

                if progressCallback:
                    progressValue = int((completedOperations / totalOperations) * 100)
                    progressCallback(
                        progressValue,
                        f"Failed sample {sampleId}: {str(exc)}"
                    )

            finally:
                if not keepLoadedNodes:
                    for node in outputNodes:
                        if node is not None and slicer.mrmlScene.IsNodePresent(node):
                            slicer.mrmlScene.RemoveNode(node)

                    for node in loadedNodes:
                        if node is not None and slicer.mrmlScene.IsNodePresent(node):
                            slicer.mrmlScene.RemoveNode(node)

                slicer.app.processEvents()

        logging.info(
            f"Batch smoothing completed. "
            f"Processed {processedPairs}/{totalPairs} pair(s). "
            f"Saved {savedOutputs} output file(s). "
            f"Failed {len(failedPairs)} pair(s)."
        )

        if progressCallback:
            progressCallback(
                100,
                (
                    f"Batch completed. Processed {processedPairs}/{totalPairs} pair(s), "
                    f"saved {savedOutputs} output file(s), "
                    f"failed {len(failedPairs)} pair(s)."
                )
            )

        return {
            "found_pairs": totalPairs,
            "processed_pairs": processedPairs,
            "saved_outputs": savedOutputs,
            "failed_pairs": failedPairs,
        }
    def readExperimentDesignByRunId(self, experimentOutputFolder):
        designCsvPath = os.path.join(experimentOutputFolder, "experiment_design.csv")
        designRows = self.readCsvRows(designCsvPath)

        designByRunId = {}

        for row in designRows:
            runId = row.get("runId", "").strip()
            if runId:
                designByRunId[runId] = row

        return designByRunId
    
    def activeParameterFromDesignRow(self, designRow):
        parameterColumns = [
            "kernelSizeMm",
            "gaussianStandardDeviationMm",
            "jointTaubinSmoothingFactor",
        ]

        for column in parameterColumns:
            value = designRow.get(column, "").strip()
            if value != "":
                try:
                    return column, float(value)
                except ValueError:
                    return column, value

        return "", ""
    
    def segmentationToBinaryArray(self, segmentationNode, referenceVolumeNode):
        """
        Export all segments to one merged binary labelmap array.

        Returns:
            binaryArray, spacing
        """

        labelmapNode = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLLabelMapVolumeNode",
            segmentationNode.GetName() + "_MetricsLabelmap"
        )

        try:
            slicer.modules.segmentations.logic().ExportAllSegmentsToLabelmapNode(
                segmentationNode,
                labelmapNode,
                referenceVolumeNode
            )

            array = slicer.util.arrayFromVolume(labelmapNode)
            binaryArray = array > 0
            spacing = referenceVolumeNode.GetSpacing()

            return binaryArray, spacing

        finally:
            if slicer.mrmlScene.IsNodePresent(labelmapNode):
                slicer.mrmlScene.RemoveNode(labelmapNode)

    def computeBinaryVolumeMm3(self, binaryArray, spacing):
            voxelVolumeMm3 = spacing[0] * spacing[1] * spacing[2]
            return float(binaryArray.sum()) * voxelVolumeMm3
    
    def computeDice(self, originalArray, outputArray):
        originalCount = int(originalArray.sum())
        outputCount = int(outputArray.sum())

        if originalCount == 0 and outputCount == 0:
            return 1.0

        if originalCount == 0 or outputCount == 0:
            return 0.0

        intersection = int((originalArray & outputArray).sum())

        return float(2.0 * intersection / (originalCount + outputCount))
    
    def computeSurfaceAreaMm2(self, segmentationNode):
        """
        Compute total closed-surface area across all segments.
        """

        segmentationNode.GetSegmentation().CreateRepresentation(
            slicer.vtkSegmentationConverter.GetSegmentationClosedSurfaceRepresentationName()
        )

        segmentation = segmentationNode.GetSegmentation()

        segmentIds = vtk.vtkStringArray()
        segmentation.GetSegmentIDs(segmentIds)

        totalArea = 0.0

        massProperties = vtk.vtkMassProperties()

        for i in range(segmentIds.GetNumberOfValues()):
            segmentId = segmentIds.GetValue(i)

            polyData = segmentation.GetSegment(segmentId).GetRepresentation(
                slicer.vtkSegmentationConverter.GetSegmentationClosedSurfaceRepresentationName()
            )

            if polyData is None:
                continue

            if polyData.GetNumberOfPoints() == 0:
                continue

            triangleFilter = vtk.vtkTriangleFilter()
            triangleFilter.SetInputData(polyData)
            triangleFilter.Update()

            massProperties.SetInputData(triangleFilter.GetOutput())
            totalArea += massProperties.GetSurfaceArea()

        return float(totalArea)
    def getSegmentCount(self, segmentationNode):
        return segmentationNode.GetSegmentation().GetNumberOfSegments()
    
    def computeMetricsForSegmentationPair(
        self,
        originalSegmentationNode,
        outputSegmentationNode,
        referenceVolumeNode,
        selectedMetrics,
    ):
        metrics = {}

        originalSegmentCount = self.getSegmentCount(originalSegmentationNode)
        outputSegmentCount = self.getSegmentCount(outputSegmentationNode)

        metrics["originalSegmentCount"] = originalSegmentCount
        metrics["outputSegmentCount"] = outputSegmentCount
        metrics["segmentCountDifference"] = outputSegmentCount - originalSegmentCount
        metrics["segmentCountPreserved"] = int(originalSegmentCount == outputSegmentCount)

        needArrays = (
            "volume" in selectedMetrics
            or "dice" in selectedMetrics
        )

        if needArrays:
            originalArray, spacing = self.segmentationToBinaryArray(
                originalSegmentationNode,
                referenceVolumeNode
            )

            outputArray, _ = self.segmentationToBinaryArray(
                outputSegmentationNode,
                referenceVolumeNode
            )

        if "volume" in selectedMetrics:
            originalVolume = self.computeBinaryVolumeMm3(originalArray, spacing)
            outputVolume = self.computeBinaryVolumeMm3(outputArray, spacing)

            if originalVolume > 0:
                volumeChangePercent = 100.0 * (outputVolume - originalVolume) / originalVolume
            else:
                volumeChangePercent = ""

            metrics["originalVolumeMm3"] = originalVolume
            metrics["outputVolumeMm3"] = outputVolume
            metrics["volumeChangePercent"] = volumeChangePercent

        if "dice" in selectedMetrics:
            metrics["diceAgainstOriginal"] = self.computeDice(
                originalArray,
                outputArray
            )

        if "surface_area" in selectedMetrics:
            originalArea = self.computeSurfaceAreaMm2(originalSegmentationNode)
            outputArea = self.computeSurfaceAreaMm2(outputSegmentationNode)

            if originalArea > 0:
                surfaceAreaChangePercent = 100.0 * (outputArea - originalArea) / originalArea
            else:
                surfaceAreaChangePercent = ""

            metrics["originalSurfaceAreaMm2"] = originalArea
            metrics["outputSurfaceAreaMm2"] = outputArea
            metrics["surfaceAreaChangePercent"] = surfaceAreaChangePercent

        return metrics
    
    def analyzeExperimentMetrics(
        self,
        dataFolder,
        experimentOutputFolder,
        selectedMetrics,
        recursive=True,
        generatePlots=True,
        progressCallback=None,
    ):
        """
        Analyze saved experiment outputs.

        Reads:
            - experiment_design.csv
            - experiment_summary.csv

        Computes selected metrics comparing each experiment output against
        the corresponding original segmentation.
        """

        if not os.path.isdir(dataFolder):
            raise ValueError(f"Original data folder does not exist: {dataFolder}")

        if not os.path.isdir(experimentOutputFolder):
            raise ValueError(f"Experiment output folder does not exist: {experimentOutputFolder}")

        if not selectedMetrics:
            raise ValueError("No metrics selected.")

        designByRunId = self.readExperimentDesignByRunId(experimentOutputFolder)

        summaryCsvPath = os.path.join(experimentOutputFolder, "experiment_summary.csv")
        summaryRows = self.readCsvRows(summaryCsvPath)

        successfulRows = [
            row for row in summaryRows
            if row.get("status", "") == "success"
            and row.get("outputPath", "").strip() != ""
        ]

        pairs = self.findVolumeSegmentationPairs(
            folderPath=dataFolder,
            recursive=recursive,
        )

        pairBySampleId = {
            pair["sampleId"]: pair
            for pair in pairs
        }

        metricsRows = []

        totalRows = len(successfulRows)
        failedRows = 0

        if progressCallback:
            progressCallback(0, f"Metrics analysis started. Rows: {totalRows}")

        for rowIndex, summaryRow in enumerate(successfulRows, start=1):
            sampleId = summaryRow.get("sampleId", "").strip()
            runId = summaryRow.get("runId", "").strip()
            outputPath = summaryRow.get("outputPath", "").strip()

            loadedNodes = []

            try:
                if sampleId not in pairBySampleId:
                    raise RuntimeError(f"No original data pair found for sample: {sampleId}")

                if runId not in designByRunId:
                    raise RuntimeError(f"No design row found for runId: {runId}")

                if not os.path.exists(outputPath):
                    raise RuntimeError(f"Output segmentation file does not exist: {outputPath}")

                pair = pairBySampleId[sampleId]
                designRow = designByRunId[runId]

                parameterName, parameterValue = self.activeParameterFromDesignRow(designRow)

                referenceVolumeNode = slicer.util.loadVolume(pair["volumePath"])
                if referenceVolumeNode is None:
                    raise RuntimeError(f"Failed to load volume: {pair['volumePath']}")

                loadedNodes.append(referenceVolumeNode)

                originalSegmentationNode = self.loadSegmentationNodeRobust(
                    segmentationPath=pair["segmentationPath"],
                    referenceVolumeNode=referenceVolumeNode,
                )
                loadedNodes.append(originalSegmentationNode)

                outputSegmentationNode = self.loadSegmentationNodeRobust(
                    segmentationPath=outputPath,
                    referenceVolumeNode=referenceVolumeNode,
                )
                loadedNodes.append(outputSegmentationNode)

                metricValues = self.computeMetricsForSegmentationPair(
                    originalSegmentationNode=originalSegmentationNode,
                    outputSegmentationNode=outputSegmentationNode,
                    referenceVolumeNode=referenceVolumeNode,
                    selectedMetrics=selectedMetrics,
                )

                metricsRow = {
                    "sampleId": sampleId,
                    "runId": runId,
                    "method": designRow.get("method", ""),
                    "name": designRow.get("name", ""),
                    "parameterName": parameterName,
                    "parameterValue": parameterValue,
                    "outputPath": outputPath,
                    "status": "success",
                    "error": "",
                }

                metricsRow.update(metricValues)
                metricsRows.append(metricsRow)

            except Exception as exc:
                failedRows += 1

                metricsRows.append({
                    "sampleId": sampleId,
                    "runId": runId,
                    "method": "",
                    "name": "",
                    "parameterName": "",
                    "parameterValue": "",
                    "outputPath": outputPath,
                    "status": "failed",
                    "error": str(exc),
                })

                logging.error(
                    f"Metrics failed for sample {sampleId}, run {runId}: {str(exc)}"
                )

            finally:
                for node in loadedNodes:
                    if node is not None and slicer.mrmlScene.IsNodePresent(node):
                        slicer.mrmlScene.RemoveNode(node)

                if progressCallback:
                    progressValue = int(rowIndex / totalRows * 100) if totalRows > 0 else 100
                    progressCallback(
                        progressValue,
                        f"Metrics row {rowIndex}/{totalRows}"
                    )

                slicer.app.processEvents()

        metricsCsvPath = os.path.join(experimentOutputFolder, "experiment_metrics.csv")
        self.saveMetricsCsv(metricsRows, metricsCsvPath)

        plotsFolder = os.path.join(experimentOutputFolder, "plots")

        if generatePlots:
            self.generateMetricPlots(
                metricsRows=metricsRows,
                plotsFolder=plotsFolder,
            )
        else:
            plotsFolder = ""

        if progressCallback:
            progressCallback(
                100,
                f"Metrics analysis completed. Failed rows: {failedRows}"
            )

        return {
            "processed_rows": len(metricsRows),
            "failed_rows": failedRows,
            "metrics_csv": metricsCsvPath,
            "plots_folder": plotsFolder,
        }
    
    def saveMetricsCsv(self, metricsRows, metricsCsvPath):
        if not metricsRows:
            raise ValueError("No metric rows to save.")

        fieldNames = [
            "sampleId",
            "runId",
            "method",
            "name",
            "parameterName",
            "parameterValue",
            "originalSegmentCount",
            "outputSegmentCount",
            "segmentCountDifference",
            "segmentCountPreserved",
            "originalVolumeMm3",
            "outputVolumeMm3",
            "volumeChangePercent",
            "diceAgainstOriginal",
            "originalSurfaceAreaMm2",
            "outputSurfaceAreaMm2",
            "surfaceAreaChangePercent",
            "outputPath",
            "status",
            "error",
        ]

        with open(metricsCsvPath, "w", newline="") as csvFile:
            writer = csv.DictWriter(csvFile, fieldnames=fieldNames, extrasaction="ignore")
            writer.writeheader()

            for row in metricsRows:
                writer.writerow(row)

        return metricsCsvPath
    
    def generateMetricPlots(self, metricsRows, plotsFolder):
        """
        Generate simple metric plots from experiment_metrics.csv rows.

        If matplotlib is unavailable, plotting is skipped but metrics CSV remains valid.
        """

        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except Exception as exc:
            logging.warning(f"Matplotlib unavailable. Skipping plots: {str(exc)}")
            return

        os.makedirs(plotsFolder, exist_ok=True)

        successfulRows = [
            row for row in metricsRows
            if row.get("status", "") == "success"
        ]

        plotDefinitions = [
            ("volumeChangePercent", "Volume change [%]", "volume_change_percent.png"),
            ("diceAgainstOriginal", "Dice against original", "dice_against_original.png"),
            ("surfaceAreaChangePercent", "Surface area change [%]", "surface_area_change_percent.png"),
            ("segmentCountDifference", "Segment count difference", "segment_count_difference.png"),
        ]

        for metricKey, yLabel, fileName in plotDefinitions:
            rows = []

            for row in successfulRows:
                value = row.get(metricKey, "")
                parameterValue = row.get("parameterValue", "")
                method = row.get("name", row.get("method", ""))

                if value == "" or parameterValue == "":
                    continue

                try:
                    rows.append(
                        {
                            "method": method,
                            "parameterValue": float(parameterValue),
                            "metricValue": float(value),
                        }
                    )
                except Exception:
                    continue

            if not rows:
                continue

            methods = sorted(set(row["method"] for row in rows))

            plt.figure(figsize=(8, 6))

            for method in methods:
                methodRows = [
                    row for row in rows
                    if row["method"] == method
                ]

                methodRows = sorted(methodRows, key=lambda r: r["parameterValue"])

                x = [row["parameterValue"] for row in methodRows]
                y = [row["metricValue"] for row in methodRows]

                plt.scatter(x, y, label=method)

            plt.xlabel("Parameter value")
            plt.ylabel(yLabel)
            plt.title(yLabel + " by smoothing parameter")
            plt.legend()
            plt.grid(True, alpha=0.3)
            plt.tight_layout()

            outputPath = os.path.join(plotsFolder, fileName)
            plt.savefig(outputPath, dpi=200)
            plt.close()
    def runSmoothingExperiment(
        self,
        inputFolder,
        outputFolder,
        experimentRuns,
        scope="ALL_SEGMENTS",
        recursive=True,
        keepLoadedNodes=False,
        progressCallback=None,
    ):
        """
        Run a smoothing experiment over all matched volume/segmentation pairs.

        Each experiment run represents one smoothing method with one parameter set.
        Each run is applied independently to the original segmentation.
        """

        if not os.path.isdir(inputFolder):
            raise ValueError(f"Input folder does not exist: {inputFolder}")

        if not experimentRuns:
            raise ValueError("No experiment runs were generated.")

        os.makedirs(outputFolder, exist_ok=True)
        inputFolderAbs = os.path.abspath(inputFolder)
        outputFolderAbs = os.path.abspath(outputFolder)

        if outputFolderAbs == inputFolderAbs:
            raise ValueError(
                "The experiment output folder cannot be the same as the input folder. "
                "Select a separate output folder."
            )

        if recursive and outputFolderAbs.startswith(inputFolderAbs + os.sep):
            raise ValueError(
                "The experiment output folder cannot be inside the input folder when recursive search is enabled. "
                "Select a separate output folder or disable recursive search."
            )

        designCsvPath = self.saveExperimentDesignCsv(
            experimentRuns=experimentRuns,
            outputFolder=outputFolder,
        )

        pairs = self.findVolumeSegmentationPairs(
            folderPath=inputFolder,
            recursive=recursive,
        )

        if not pairs:
            raise ValueError(
                "No matching volume/segmentation pairs were found. "
                "Expected names such as Volume_001.nrrd and Segmentation_001.seg.nrrd."
            )

        summaryRows = []

        totalOperations = len(pairs) * len(experimentRuns)
        completedOperations = 0

        if progressCallback:
            progressCallback(
                0,
                f"Experiment started. {len(pairs)} sample(s), {len(experimentRuns)} run(s)."
            )

        for pairIndex, pair in enumerate(pairs, start=1):
            sampleId = pair["sampleId"]
            volumePath = pair["volumePath"]
            segmentationPath = pair["segmentationPath"]

            loadedNodes = []
            outputNodes = []

            try:
                referenceVolumeNode = slicer.util.loadVolume(volumePath)

                if referenceVolumeNode is None:
                    raise RuntimeError(f"Failed to load volume: {volumePath}")

                loadedNodes.append(referenceVolumeNode)

                segmentationNode = self.loadSegmentationNodeRobust(
                    segmentationPath=segmentationPath,
                    referenceVolumeNode=referenceVolumeNode,
                )

                if segmentationNode is None:
                    raise RuntimeError(f"Failed to load segmentation: {segmentationPath}")

                segmentationNode.CreateDefaultDisplayNodes()
                loadedNodes.append(segmentationNode)

                if segmentationNode.GetSegmentation().GetNumberOfSegments() == 0:
                    raise RuntimeError(
                        f"Segmentation has no segments: {segmentationPath}"
                    )

                for run in experimentRuns:
                    runStartTime = time.time()

                    runId = run["runId"]
                    method = run["method"]
                    methodName = run.get("name", method)
                    safeMethodName = self.safeNodeName(methodName)

                    runOutputFolder = os.path.join(outputFolder, runId)
                    os.makedirs(runOutputFolder, exist_ok=True)

                    outputName = (
                        f"Segmentation_{sampleId}_{runId}_{safeMethodName}"
                    )

                    outputNode = slicer.mrmlScene.AddNewNodeByClass(
                        "vtkMRMLSegmentationNode",
                        outputName,
                    )

                    outputNodes.append(outputNode)

                    try:
                        self.copySegmentationContent(
                            inputSegmentationNode=segmentationNode,
                            outputSegmentationNode=outputNode,
                            outputName=outputName,
                        )

                        self.smoothSegmentation(
                            segmentationNode=outputNode,
                            referenceVolumeNode=referenceVolumeNode,
                            method=method,
                            scope=scope,
                            kernelSizeMm=run.get("kernelSizeMm", 3.0),
                            gaussianStandardDeviationMm=run.get(
                                "gaussianStandardDeviationMm", 1.0
                            ),
                            jointTaubinSmoothingFactor=run.get(
                                "jointTaubinSmoothingFactor", 0.5
                            ),
                        )

                        outputFileName = f"{outputName}.seg.nrrd"
                        outputPath = os.path.join(runOutputFolder, outputFileName)

                        success = slicer.util.saveNode(outputNode, outputPath)

                        if not success:
                            raise RuntimeError(f"Failed to save output: {outputPath}")

                        status = "success"
                        error = ""

                    except Exception as exc:
                        outputPath = ""
                        status = "failed"
                        error = str(exc)
                        logging.error(
                            f"Experiment failed for sample {sampleId}, run {runId}: {error}"
                        )

                    runStopTime = time.time()

                    summaryRows.append({
                        "runId": runId,
                        "sampleId": sampleId,
                        "method": method,
                        "name": methodName,
                        "kernelSizeMm": run.get("kernelSizeMm", ""),
                        "gaussianStandardDeviationMm": run.get(
                            "gaussianStandardDeviationMm", ""
                        ),
                        "jointTaubinSmoothingFactor": run.get(
                            "jointTaubinSmoothingFactor", ""
                        ),
                        "outputPath": outputPath,
                        "status": status,
                        "error": error,
                        "processingTimeSec": f"{runStopTime - runStartTime:.4f}",
                    })

                    completedOperations += 1

                    if progressCallback:
                        progressValue = int(
                            completedOperations / totalOperations * 100
                        )
                        progressCallback(
                            progressValue,
                            (
                                f"Sample {sampleId}, {runId}: {methodName}. "
                                f"{completedOperations}/{totalOperations}"
                            )
                        )

            except Exception as exc:
                logging.error(f"Failed to process sample {sampleId}: {str(exc)}")

                for run in experimentRuns:
                    summaryRows.append({
                        "runId": run["runId"],
                        "sampleId": sampleId,
                        "method": run["method"],
                        "name": run.get("name", run["method"]),
                        "kernelSizeMm": run.get("kernelSizeMm", ""),
                        "gaussianStandardDeviationMm": run.get(
                            "gaussianStandardDeviationMm", ""
                        ),
                        "jointTaubinSmoothingFactor": run.get(
                            "jointTaubinSmoothingFactor", ""
                        ),
                        "outputPath": "",
                        "status": "failed",
                        "error": str(exc),
                        "processingTimeSec": "0.0000",
                    })

                    completedOperations += 1

            finally:
                if not keepLoadedNodes:
                    for node in outputNodes:
                        if node is not None and slicer.mrmlScene.IsNodePresent(node):
                            slicer.mrmlScene.RemoveNode(node)

                    for node in loadedNodes:
                        if node is not None and slicer.mrmlScene.IsNodePresent(node):
                            slicer.mrmlScene.RemoveNode(node)

                slicer.app.processEvents()

        summaryCsvPath = os.path.join(outputFolder, "experiment_summary.csv")

        fieldNames = [
            "runId",
            "sampleId",
            "method",
            "name",
            "kernelSizeMm",
            "gaussianStandardDeviationMm",
            "jointTaubinSmoothingFactor",
            "outputPath",
            "status",
            "error",
            "processingTimeSec",
        ]

        with open(summaryCsvPath, "w", newline="") as csvFile:
            writer = csv.DictWriter(csvFile, fieldnames=fieldNames)
            writer.writeheader()
            writer.writerows(summaryRows)

        successfulRuns = len([
            row for row in summaryRows
            if row["status"] == "success"
        ])

        failedRuns = len([
            row for row in summaryRows
            if row["status"] == "failed"
        ])

        if progressCallback:
            progressCallback(
                100,
                (
                    f"Experiment completed. Success: {successfulRuns}, "
                    f"failed: {failedRuns}."
                )
            )

        return {
            "found_pairs": len(pairs),
            "experiment_runs": len(experimentRuns),
            "successful_outputs": successfulRuns,
            "failed_outputs": failedRuns,
            "design_csv": designCsvPath,
            "summary_csv": summaryCsvPath,
        }

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
        pairs = logic.findVolumeSegmentationPairs(
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

    def _sampleIdFromFileName(self, filePath):
        """
        Extract sample ID from file names such as:

            Volume_001.nrrd
            Volume_001.nii.gz
            Segmentation_001.seg.nrrd
            Segmentation_001.nrrd

        Returns:
            "001"
        """

        import os
        import re

        fileName = os.path.basename(filePath)

        match = re.search(
            r"^(Volume|Segmentation)_(.+?)(\.seg)?(\.nii\.gz|\.nii|\.nrrd|\.nhdr|\.mha|\.mhd|\.vtk|\.vtm)$",
            fileName,
            flags=re.IGNORECASE,
        )

        if not match:
            return None

        return match.group(2)


    def _findVolumeSegmentationPairs(self, folderPath, recursive=True):
        """
        Find matching volume/segmentation pairs in a folder.

        Matching rule:
            Volume_XXX.ext
            Segmentation_XXX.ext
        """

        if not os.path.isdir(folderPath):
            raise ValueError(f"Folder does not exist: {folderPath}")

        allFiles = self.collectFilesFromFolder(folderPath, recursive=recursive)

        logging.info(f"[BATCH PAIRING] Folder: {folderPath}")
        logging.info(f"[BATCH PAIRING] Recursive: {recursive}")
        logging.info(f"[BATCH PAIRING] Total files found: {len(allFiles)}")

        volumeFiles = [
            filePath for filePath in allFiles
            if self.isVolumeFile(filePath)
        ]

        segmentationFiles = [
            filePath for filePath in allFiles
            if self.isSegmentationFile(filePath)
        ]

        logging.info(f"[BATCH PAIRING] Candidate volume files: {len(volumeFiles)}")
        for filePath in volumeFiles:
            logging.info(f"[BATCH PAIRING] Volume candidate: {os.path.basename(filePath)}")

        logging.info(f"[BATCH PAIRING] Candidate segmentation files: {len(segmentationFiles)}")
        for filePath in segmentationFiles:
            logging.info(f"[BATCH PAIRING] Segmentation candidate: {os.path.basename(filePath)}")

        segmentationBySampleId = {}

        for segmentationFile in segmentationFiles:
            sampleId = self.sampleIdFromFileName(segmentationFile)

            logging.info(
                f"[BATCH PAIRING] Segmentation sample ID: "
                f"{os.path.basename(segmentationFile)} -> {sampleId}"
            )

            if sampleId is not None:
                segmentationBySampleId.setdefault(sampleId, []).append(segmentationFile)

        pairs = []

        for volumeFile in volumeFiles:
            sampleId = self.sampleIdFromFileName(volumeFile)

            logging.info(
                f"[BATCH PAIRING] Volume sample ID: "
                f"{os.path.basename(volumeFile)} -> {sampleId}"
            )

            if sampleId is None:
                continue

            if sampleId in segmentationBySampleId:
                for segmentationFile in segmentationBySampleId[sampleId]:
                    pairs.append(
                        {
                            "sampleId": sampleId,
                            "volumePath": volumeFile,
                            "segmentationPath": segmentationFile,
                        }
                    )

                    logging.info(
                        f"[BATCH PAIRING] Pair found: "
                        f"{os.path.basename(volumeFile)} + "
                        f"{os.path.basename(segmentationFile)}"
                    )

        logging.info(f"[BATCH PAIRING] Total pairs found: {len(pairs)}")

        return pairs