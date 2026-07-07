import os
import csv
import time
import random
import itertools
import logging
import slicer

from .constants import EXPERIMENT_FIELD_NAMES, EXPERIMENT_SUMMARY_FIELD_NAMES
from .file_utils import FileUtils
from .segmentation_utils import SegmentationUtils
from .smoothing_logic import SmoothingEngine
from .batch_processor import BatchProcessor


class ExperimentProcessor:
    def __init__(self):
        self.smoothingEngine = SmoothingEngine()
        self.batchProcessor = BatchProcessor()

    
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

        pairs = FileUtils.findVolumeSegmentationPairs(
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

                segmentationNode = SegmentationUtils.loadSegmentationNodeRobust(
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
                    safeMethodName = FileUtils.safeNodeName(methodName)

                    runOutputFolder = os.path.join(outputFolder, runId)
                    os.makedirs(runOutputFolder, exist_ok=True)

                    outputName = (
                        f"Segmentation_{sampleId}_{runId}_{safeMethodName}"
                    )

                    outputNode = slicer.mrmlScene.AddNewNodeByClass(
                        "vtkMRMLSegmentationNode",
                        outputName,
                    )

                    try:
                        SegmentationUtils.copySegmentationContent(
                            inputSegmentationNode=segmentationNode,
                            outputSegmentationNode=outputNode,
                            outputName=outputName,
                            createDisplayNode=keepLoadedNodes,
                        )

                        SmoothingEngine.smoothSegmentation(
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
                        outputPath = os.path.abspath(
                            os.path.join(runOutputFolder, outputFileName)
                        )

                        success = slicer.util.saveNode(outputNode, outputPath)

                        if not success:
                            raise RuntimeError(f"Failed to save output: {outputPath}")

                        status = "success"
                        error = ""

                        if keepLoadedNodes:
                            outputNodes.append(outputNode)
                        else:
                            slicer.mrmlScene.RemoveNode(outputNode)
                            outputNode = None

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

                    if progressCallback and BatchProcessor.shouldUpdateProgress(
                        completedOperations,
                        totalOperations,
                        everyPercent=2,
                    ):
                        progressValue = int(completedOperations / totalOperations * 100)
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