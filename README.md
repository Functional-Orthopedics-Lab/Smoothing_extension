# Smoothing Extension for 3D Slicer

## Overview

The Smoothing extension is a 3D Slicer scripted module for post-processing segmentation nodes. It provides a graphical interface for applying one or more smoothing methods to medical image segmentations.

The extension uses 3D Slicer’s built-in Segment Editor smoothing functionality and exposes it through a simplified interface. It can be used for:

- smoothing a single segmentation already loaded in 3D Slicer;
- comparing different smoothing algorithms on the same original segmentation;
- batch-processing multiple volume/segmentation pairs stored in a folder.

Important: when several smoothing methods are selected, each method is applied independently to a copy of the original segmentation. The methods are not applied sequentially on top of each other.

For example, if Median, Gaussian, and Joint Taubin are selected, the extension creates:

Original segmentation
├── Median-smoothed segmentation
├── Gaussian-smoothed segmentation
└── Joint-Taubin-smoothed segmentation

It does not create:

Original segmentation → Median → Gaussian → Joint Taubin

This design is useful when the physician or researcher wants to compare the effect of different smoothing algorithms under the same initial conditions.

---

## Requirements

This extension requires:

- 3D Slicer installed on the computer.
- A local copy of this repository.
- Medical image data already readable by 3D Slicer.
- Segmentation files readable as either:
  - Slicer segmentation files, such as `.seg.nrrd`; or
  - labelmap volumes, such as `.nrrd`, `.nii`, or `.nii.gz`.

The extension depends on standard 3D Slicer modules:

- Segmentations
- Segment Editor

No additional Python packages are required for the core smoothing workflow.

---

## Downloading the repository

### Option 1: Download as ZIP

1. Open the repository page in your web browser.
2. Click the green `Code` button.
3. Select `Download ZIP`.
4. Extract the ZIP file to a known location on your computer.

Example locations:

Linux:

```bash
/home/username/SlicerExtensions/Smoothing_extension