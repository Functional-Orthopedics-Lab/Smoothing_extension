# Smoothing Extension for 3D Slicer

## Overview

The **Smoothing** extension is a 3D Slicer module for smoothing medical image segmentations.

It allows the user to apply one or more smoothing methods to a segmentation. When several methods are selected, each method is applied independently to a copy of the original segmentation. This makes it possible to compare different smoothing results without modifying the original segmentation.

---

## Installation

### 1. Download the repository

Download or clone the repository:

```bash
git clone <REPOSITORY_URL>
```

Replace `<REPOSITORY_URL>` with the repository URL.

Example:

```bash
git clone https://github.com/your-user/Smoothing_extension.git
```

---

### 2. Add the module to 3D Slicer

Open **3D Slicer**.

Go to:

```text
Edit → Application Settings → Modules
```

Under **Additional module paths**, click **Add** and select the module folder:

```text
Smoothing_extension/Smoothing
```

Click **OK** and restart 3D Slicer.

After restarting, open the module from:

```text
Modules → Segmentation → Smoothing
```

---

## How to Use the Extension

### 1. Load your data

Load the image volume and its segmentation into 3D Slicer.

Recommended formats:

```text
Volume: .nrrd, .nii, .nii.gz, .mha, .mhd
Segmentation: .seg.nrrd
```

---

### 2. Select inputs

In the **Inputs** section:

1. Select the **Input segmentation**.
2. Select the corresponding **Reference volume**.

---

### 3. Choose smoothing methods

Select one or more smoothing methods:

* **Median**
* **Opening**
* **Closing**
* **Gaussian**
* **Joint Taubin**

By default, **Joint Taubin** is selected.

When multiple methods are selected, each one is applied separately to the original segmentation.

---

### 4. Adjust parameters

Each method has its own parameter tab.

Default values:

```text
Median kernel size: 3.0 mm
Opening kernel size: 3.0 mm
Closing kernel size: 3.0 mm
Gaussian standard deviation: 1.0 mm
Joint Taubin smoothing factor: 0.5
```

For clinical review, start with conservative values and visually inspect the result.

---

### 5. Choose application scope

Select whether smoothing should be applied to:

```text
Visible segments
```

or:

```text
All segments
```

Use **Visible segments** if only selected structures should be processed.

Use **All segments** if the complete segmentation should be smoothed.

---

### 6. Choose output

To preserve the original segmentation, leave **Overwrite input** unchecked and select or create an **Output segmentation**.

Click:

```text
Apply smoothing
```

The smoothed segmentation will be created in the Slicer scene.

---

## Batch Processing

Batch processing allows multiple volume-segmentation pairs to be processed automatically.

### Required file naming

Files must follow this naming pattern:

```text
Volume_<ID>.<extension>
Segmentation_<ID>.<extension>
```

Example:

```text
InputFolder/
├── Volume_001.nrrd
├── Segmentation_001.seg.nrrd
├── Volume_002.nrrd
└── Segmentation_002.seg.nrrd
```

The ID must match exactly.

This works:

```text
Volume_001.nrrd
Segmentation_001.seg.nrrd
```

This does not work:

```text
Volume_0001.nrrd
Segmentation_001.seg.nrrd
```

---

### How to run batch processing

1. Open the **Batch processing** section.
2. Check:

```text
Enable batch processing from folder
```

3. Select the **Input folder**.
4. Select a separate **Output folder**.
5. Select the smoothing methods.
6. Click:

```text
Apply smoothing
```

The output folder will contain files such as:

```text
Segmentation_001_Joint_Taubin_smoothed.seg.nrrd
Segmentation_002_Joint_Taubin_smoothed.seg.nrrd
```

If multiple methods are selected, one output file is generated per method.

---

## Recommended First Test

Before processing many cases, test one pair:

```text
InputFolder/
├── Volume_001.nrrd
└── Segmentation_001.seg.nrrd
```

Use:

```text
Joint Taubin
Apply to: All segments
```

Expected output:

```text
OutputFolder/
└── Segmentation_001_Joint_Taubin_smoothed.seg.nrrd
```

---

## Clinical Note

This extension is intended for segmentation post-processing and visual review.

The output should always be checked by a qualified physician, radiologist, surgeon, or trained imaging specialist before clinical or research use.

Smoothing may improve visual quality, but excessive smoothing can alter anatomical boundaries.

---

## Troubleshooting

### The module does not appear

Check that the added module path is:

```text
Smoothing_extension/Smoothing
```

Then restart 3D Slicer.

---

### The Apply button is disabled

Check that:

* an input segmentation is selected,
* a reference volume is selected,
* at least one smoothing method is selected,
* an output segmentation is selected if overwrite is disabled,
* in batch mode, valid input and output folders are selected.

---

### Batch processing finds no files

Check that the files are named exactly as:

```text
Volume_<ID>
Segmentation_<ID>
```

Example:

```text
Volume_001.nrrd
Segmentation_001.seg.nrrd
```

---

### Processing is slow

Processing time depends on segmentation size, number of segments, smoothing method, and parameter values.

For faster testing:

* start with one case,
* use Joint Taubin first,
* avoid very large kernel sizes,
* keep **Keep loaded batch nodes in scene** unchecked.

---

## License

Add license information here.
