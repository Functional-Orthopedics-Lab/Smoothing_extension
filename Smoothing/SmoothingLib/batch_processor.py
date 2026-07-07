import os
import logging
import slicer

from .file_utils import FileUtils
from .segmentation_utils import SegmentationUtils
from .smoothing_logic import SmoothingEngine


class BatchProcessor:
    def __init__(self):
        self.smoothingEngine = SmoothingEngine()

    def shouldUpdateProgress(self, completedOperations, totalOperations, everyPercent=2):
        """
        Return True only when the progress percentage has advanced enough.

        This avoids updating the Slicer GUI after every single operation,
        which can slow large batch/experiment runs.
        """

        if totalOperations <= 0:
            return True

        if completedOperations == 0 or completedOperations == totalOperations:
            return True

        previousPercent = int((completedOperations - 1) / totalOperations * 100)
        currentPercent = int(completedOperations / totalOperations * 100)

        return currentPercent >= previousPercent + everyPercent
    
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

        pairs = FileUtils.findVolumeSegmentationPairs(
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

                segmentationNode = SegmentationUtils.loadSegmentationNodeRobust(
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
                    safeMethodName = FileUtils.safeNodeName(methodName)

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

                    SegmentationUtils.copySegmentationContent(
                        inputSegmentationNode=segmentationNode,
                        outputSegmentationNode=outputNode,
                        outputName=outputName,
                        createDisplayNode=keepLoadedNodes,
                    )

                    SmoothingEngine.smoothSegmentation(
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

                    outputFileName = (
                        f"Segmentation_{sampleId}_{safeMethodName}_smoothed.seg.nrrd"
                    )

                    outputPath = os.path.abspath(
                        os.path.join(outputFolder, outputFileName)
                    )

                    success = slicer.util.saveNode(outputNode, outputPath)

                    if not success:
                        raise RuntimeError(f"Failed to save output: {outputPath}")
                    
                    logging.info(f"Saved: {outputPath}")
                    savedOutputs += 1

                    if keepLoadedNodes:
                        outputNodes.append(outputNode)
                    else:
                        slicer.mrmlScene.RemoveNode(outputNode)
                        outputNode = None
                        
                    completedOperations += 1

                    if progressCallback and self.shouldUpdateProgress(
                        completedOperations,
                        totalOperations,
                        everyPercent=2,
                    ):
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
    