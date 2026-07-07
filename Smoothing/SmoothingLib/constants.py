SMOOTHING_METHODS = {
    "MEDIAN": {
        "name": "Median",
        "parameter": "kernelSizeMm",
        "effectParameter": "KernelSizeMm",
    },
    "MORPHOLOGICAL_OPENING": {
        "name": "Opening",
        "parameter": "kernelSizeMm",
        "effectParameter": "KernelSizeMm",
    },
    "MORPHOLOGICAL_CLOSING": {
        "name": "Closing",
        "parameter": "kernelSizeMm",
        "effectParameter": "KernelSizeMm",
    },
    "GAUSSIAN": {
        "name": "Gaussian",
        "parameter": "gaussianStandardDeviationMm",
        "effectParameter": "GaussianStandardDeviationMm",
    },
    "JOINT_TAUBIN": {
        "name": "Joint Taubin",
        "parameter": "jointTaubinSmoothingFactor",
        "effectParameter": "JointTaubinSmoothingFactor",
    },
}

EXPERIMENT_FIELD_NAMES = [
    "runId",
    "method",
    "name",
    "kernelSizeMm",
    "gaussianStandardDeviationMm",
    "jointTaubinSmoothingFactor",
]

EXPERIMENT_SUMMARY_FIELD_NAMES = [
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

METRICS_FIELD_NAMES = [
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