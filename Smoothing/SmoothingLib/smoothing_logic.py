import time
import logging
import slicer

from .segmentation_utils import SegmentationUtils
from .file_utils import FileUtils

class SmoothingEngine:
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

        originalVisibleSegmentIds = SegmentationUtils.visibleSegmentIds(segmentationNode)

        try:
            if scope == "ALL_SEGMENTS":
                SegmentationUtils.setAllSegmentsVisible(segmentationNode, True)

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
                SegmentationUtils.restoreVisibleSegments(segmentationNode, originalVisibleSegmentIds)

            segmentEditorWidget.setMRMLSegmentEditorNode(None)
            slicer.mrmlScene.RemoveNode(segmentEditorNode)
            segmentEditorWidget = None

        stopTime = time.time()
        logging.info(
            f"Segmentation smoothing step completed in {stopTime - startTime:.2f} seconds"
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
            safeMethodName = FileUtils.safeNodeName(methodName)

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

            SegmentationUtils.copySegmentationContent(
                inputSegmentationNode=inputSegmentationNode,
                outputSegmentationNode=outputNode,
                outputName=outputName,
                createDisplayNode=True
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