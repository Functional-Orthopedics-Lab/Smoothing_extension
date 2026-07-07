import os
import re
import csv
import logging


class FileUtils:
    @staticmethod
    def readCsvRows(csvPath):
        if not os.path.exists(csvPath):
            raise ValueError(f"CSV file does not exist: {csvPath}")

        with open(csvPath, newline="") as csvFile:
            return list(csv.DictReader(csvFile))

    @staticmethod
    def safeNodeName(name: str) -> str:
        return (
            name.strip()
            .replace(" ", "_")
            .replace("/", "_")
            .replace("\\", "_")
            .replace("(", "")
            .replace(")", "")
        )

    @staticmethod
    def isSegmentationFile(filePath):
        fileName = os.path.basename(filePath).lower()

        if ".seg." in fileName:
            return True

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

    @staticmethod
    def isVolumeFile(filePath):
        fileName = os.path.basename(filePath).lower()

        if FileUtils.isSegmentationFile(filePath):
            return False

        volumeExtensions = [
            ".nii",
            ".nii.gz",
            ".nrrd",
            ".nhdr",
            ".mha",
            ".mhd",
        ]

        return any(fileName.endswith(ext) for ext in volumeExtensions)

    @staticmethod
    def sampleIdFromFileName(filePath):
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

    @staticmethod
    def collectFilesFromFolder(folderPath, recursive=True):
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

    @staticmethod
    def findVolumeSegmentationPairs(folderPath, recursive=True):
        if not os.path.isdir(folderPath):
            raise ValueError(f"Folder does not exist: {folderPath}")

        allFiles = FileUtils.collectFilesFromFolder(folderPath, recursive=recursive)

        volumeFiles = [
            filePath for filePath in allFiles
            if FileUtils.isVolumeFile(filePath)
        ]

        segmentationFiles = [
            filePath for filePath in allFiles
            if FileUtils.isSegmentationFile(filePath)
        ]

        segmentationBySampleId = {}

        for segmentationFile in segmentationFiles:
            sampleId = FileUtils.sampleIdFromFileName(segmentationFile)

            if sampleId is not None:
                segmentationBySampleId.setdefault(sampleId, []).append(segmentationFile)

        pairs = []

        for volumeFile in volumeFiles:
            sampleId = FileUtils.sampleIdFromFileName(volumeFile)

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

        logging.info(f"[PAIRING] Total pairs found: {len(pairs)}")
        return pairs