# Camera Identification

```bash
pip install -e ".[prnu]"                               # adds PyWavelets
exif-inspect prnu build -o myphone.npz flat/*.jpg      # fingerprint from 20 or more photos
exif-inspect prnu match myphone.npz questioned/*.jpg   # PCE score per photo
exif-inspect prnu locate myphone.npz edited.jpg        # map where the pattern is missing
```

Every pixel of an image sensor converts light with a slightly different efficiency. The differences are fixed for the life of the sensor and leave the same faint multiplicative pattern, the photo-response non-uniformity (PRNU), in every picture. It is a fingerprint of one physical camera, telling apart two units of the same model (Lukas, Fridrich and Goljan, 2006; Chen et al., 2008).

## The three steps

1. **Noise residual**: the image minus a denoised copy (4-level `db4` wavelet transform, local Wiener filter on the detail bands). Row and column means are removed on each colour-filter subgrid and spectral peaks are flattened, which strips patterns that every camera of a model shares.
2. **Fingerprint**: from N photos of one camera, `K = sum(W_i * I_i) / sum(I_i^2)`, skipping clipped pixels. Bright, flat, slightly defocused shots (sky, a white wall) give the cleanest estimate. `prnu build` keeps only the most common resolution and lists the photos it skipped.
3. **Match**: the residual of a questioned photo is correlated with `I * K` over all cyclic shifts, and the peak-to-correlation energy (PCE) measures how sharply it peaks at zero shift; four rotations are tried. PCE of 60 or more reads as the same camera, 20 to 60 as inconclusive, below 20 as no evidence.

The analysis uses a 2048-pixel centre crop (`--crop`; 0 for the largest centred square), so a fingerprint only fits photos taken at the same resolution, and `match` and `locate` refuse others.

## What the numbers look like

On the Dresden Image Database subset distributed with prnu-python (six cameras, two units each of three Nikon models, 1024-pixel crop):

| | PCE, same camera | PCE, other cameras |
|---|---|---|
| median | 119 | 0.7 |
| range | 11 to 1257 | up to 14.9 |
| at or above 60 | 14 of 20 | 0 of 100 |

The highest score always belonged to the right camera, including against the second unit of the same model. Processing erodes the pattern quickly: re-saved at JPEG quality 76 (WeChat without "Original") the median fell to 43, at quality 50 to 22, and resized to 90% to 0.9.

An iPhone 16 Pro main camera at 24 megapixels scored 937 to 1449 on everyday photos once fingerprint and photos shared one resolution; the ultrawide camera of the same phone scored 12.8 to 21.5 against it, a separate sensor with a little shared processing. Each lens of a multi-camera phone has its own fingerprint; night mode, HDR merges and digital stabilisation blur it; build the fingerprint from photos of the same lens, resolution and mode as the questioned photo.

## Finding replaced areas

An area pasted in, retouched or filled by AI never passed through the sensor, so it lacks the pattern. `prnu locate` correlates the residual with `I * K` in overlapping blocks and compares each block with what the model `W = I*K + noise` predicts for it, with a median factor that a minority of tampered blocks cannot pull. Blocks far below their prediction are marked red; dark, clipped and busy blocks, where even an untouched image shows little pattern, are dimmed as "cannot judge".

On the Dresden subset, a 384-pixel square blurred in place (a stand-in for an AI fill) was found in 13 of 16 photos, a square from another camera's photo in 2 of 16 (busy content from another camera looks like busy content from this one), and an untouched photo produced false marks above 1% of the crop in 1 of 20.

A real AI removal did not show: Photos' Clean Up removed a white plastic bag and a book edge from an iPhone photo, but those areas carried only a sixth of the photo's usual pattern before anything was edited, because the phone's multi-frame processing had smeared it on bright glossy surfaces. The edit was given away only by its metadata (`ai_composite_marker`). PRNU localisation needs the replaced area to have had a clear pattern to begin with.
