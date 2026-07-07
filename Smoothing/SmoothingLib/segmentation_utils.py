import os
import logging
import vtk
import slicer


class SegmentationUtils:
    @staticmethod
    def loadSegmentationNodeRobust(segmentationPath, referenceVolumeNode=None):
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
    

    @staticmethod
    def copySegmentationContent(
        inputSegmentationNode,
        outputSegmentationNode,
        outputName=None,
        createDisplayNode=False,
    ):
        if inputSegmentationNode is None or outputSegmentationNode is None:
            raise ValueError("Input or output segmentation node is invalid.")

        if outputName:
            outputSegmentationNode.SetName(outputName)

        outputSegmentationNode.RemoveAllDisplayNodeIDs()
        outputSegmentationNode.GetSegmentation().RemoveAllSegments()

        outputSegmentationNode.GetSegmentation().DeepCopy(
            inputSegmentationNode.GetSegmentation()
        )

        outputSegmentationNode.SetAndObserveTransformNodeID(
            inputSegmentationNode.GetTransformNodeID()
        )

        if createDisplayNode:
            outputDisplayNode = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLSegmentationDisplayNode",
                outputSegmentationNode.GetName() + "_Display"
            )

            outputSegmentationNode.SetAndObserveDisplayNodeID(outputDisplayNode.GetID())

            outputDisplayNode.SetVisibility(True)
            outputDisplayNode.SetVisibility3D(True)
            outputDisplayNode.SetVisibility2DFill(True)
            outputDisplayNode.SetVisibility2DOutline(True)

    @staticmethod
    def visibleSegmentIds(segmentationNode):
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

    @staticmethod
    def setAllSegmentsVisible(segmentationNode, visible=True):
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

    @staticmethod
    def restoreVisibleSegments(segmentationNode, visibleSegmentIds):
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

    @staticmethod
    def segmentationToBinaryArray(segmentationNode, referenceVolumeNode):
        """
        Export all segments to one merged binary labelmap array.

        Returns:
            binaryArray, spacing
        """

        if segmentationNode is None:
            raise ValueError("Segmentation node is invalid.")

        if referenceVolumeNode is None:
            raise ValueError("Reference volume node is invalid.")

        labelmapNode = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLLabelMapVolumeNode",
            segmentationNode.GetName() + "_MetricsLabelmap"
        )

        try:
            segmentationNode.SetReferenceImageGeometryParameterFromVolumeNode(
                referenceVolumeNode
            )

            segmentIds = vtk.vtkStringArray()
            segmentationNode.GetSegmentation().GetSegmentIDs(segmentIds)

            slicer.modules.segmentations.logic().ExportSegmentsToLabelmapNode(
                segmentationNode,
                segmentIds,
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

    @staticmethod
    def computeBinaryVolumeMm3(binaryArray, spacing):
            voxelVolumeMm3 = spacing[0] * spacing[1] * spacing[2]
            return float(binaryArray.sum()) * voxelVolumeMm3
    
    @staticmethod
    def computeDice(originalArray, outputArray):
        originalCount = int(originalArray.sum())
        outputCount = int(outputArray.sum())

        if originalCount == 0 and outputCount == 0:
            return 1.0

        if originalCount == 0 or outputCount == 0:
            return 0.0

        intersection = int((originalArray & outputArray).sum())

        return float(2.0 * intersection / (originalCount + outputCount))
    
    @staticmethod
    def computeSurfaceAreaMm2(segmentationNode):
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
    
    @staticmethod
    def getSegmentCount(segmentationNode):
        return segmentationNode.GetSegmentation().GetNumberOfSegments()