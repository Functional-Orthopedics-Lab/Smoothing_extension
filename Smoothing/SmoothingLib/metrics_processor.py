import os
import csv
import logging
import slicer

from .constants import METRICS_FIELD_NAMES
from .file_utils import FileUtils
from .segmentation_utils import SegmentationUtils


class MetricsProcessor:
    @staticmethod
    def readExperimentDesignByRunId(experimentOutputFolder):
        designCsvPath = os.path.join(experimentOutputFolder, "experiment_design.csv")
        designRows = FileUtils.readCsvRows(designCsvPath)

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

    
    
    def computeMetricsForSegmentationPair(
        self,
        originalSegmentationNode,
        outputSegmentationNode,
        referenceVolumeNode,
        selectedMetrics,
    ):
        metrics = {}

        originalSegmentCount = SegmentationUtils.getSegmentCount(originalSegmentationNode)
        outputSegmentCount = SegmentationUtils.getSegmentCount(outputSegmentationNode)

        metrics["originalSegmentCount"] = originalSegmentCount
        metrics["outputSegmentCount"] = outputSegmentCount
        metrics["segmentCountDifference"] = outputSegmentCount - originalSegmentCount
        metrics["segmentCountPreserved"] = int(originalSegmentCount == outputSegmentCount)

        needArrays = (
            "volume" in selectedMetrics
            or "dice" in selectedMetrics
        )

        if needArrays:
            originalArray, spacing = SegmentationUtils.segmentationToBinaryArray(
                originalSegmentationNode,
                referenceVolumeNode
            )

            outputArray, _ = SegmentationUtils.segmentationToBinaryArray(
                outputSegmentationNode,
                referenceVolumeNode
            )

        if "volume" in selectedMetrics:
            originalVolume = SegmentationUtils.computeBinaryVolumeMm3(originalArray, spacing)
            outputVolume = SegmentationUtils.computeBinaryVolumeMm3(outputArray, spacing)

            if originalVolume > 0:
                volumeChangePercent = 100.0 * (outputVolume - originalVolume) / originalVolume
            else:
                volumeChangePercent = ""

            metrics["originalVolumeMm3"] = originalVolume
            metrics["outputVolumeMm3"] = outputVolume
            metrics["volumeChangePercent"] = volumeChangePercent

        if "dice" in selectedMetrics:
            metrics["diceAgainstOriginal"] = SegmentationUtils.computeDice(
                originalArray,
                outputArray
            )

        if "surface_area" in selectedMetrics:
            originalArea = SegmentationUtils.computeSurfaceAreaMm2(originalSegmentationNode)
            outputArea = SegmentationUtils.computeSurfaceAreaMm2(outputSegmentationNode)

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
        summaryRows = FileUtils.readCsvRows(summaryCsvPath)

        successfulRows = [
            row for row in summaryRows
            if row.get("status", "") == "success"
            and row.get("outputPath", "").strip() != ""
        ]

        pairs = FileUtils.findVolumeSegmentationPairs(
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

                originalSegmentationNode = SegmentationUtils.loadSegmentationNodeRobust(
                    segmentationPath=pair["segmentationPath"],
                    referenceVolumeNode=referenceVolumeNode,
                )
                loadedNodes.append(originalSegmentationNode)

                outputSegmentationNode = SegmentationUtils.loadSegmentationNodeRobust(
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
                    "method": designRow.get("method", ""),
                    "name": designRow.get("name", ""),
                    "parameterName": parameterName,
                    "parameterValue": parameterValue,
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
