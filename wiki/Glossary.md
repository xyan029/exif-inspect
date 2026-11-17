# Glossary

- **APP1, APP11**: application segments in a JPEG file. APP1 carries EXIF and XMP; APP11 carries JUMBF boxes such as C2PA manifests.
- **Bayer filter, CFA**: the colour filter array over a sensor. Each pixel records one colour; demosaicing fills in the other two from neighbours.
- **C2PA**: Content Credentials, a signed manifest of who made a file and what was done to it. exif-inspect reports its presence and generator but does not verify the signature.
- **Demosaicing**: interpolation of the two missing colours per pixel. It leaves a periodic pattern that `cfa` measures.
- **ELA, error level analysis**: re-save the picture once and look at how much each pixel changes; areas with a different compression history change differently.
- **Gain map**: a half-size grayscale image stored with HDR photos (iPhone 12 and later, Ultra HDR) that tells HDR displays where to brighten.
- **Ghost, JPEG ghost**: a dip in the re-save error at a quality the picture passed through before.
- **IPTC digital source type**: a metadata field whose values `trainedAlgorithmicMedia` and `compositeWithTrainedAlgorithmicMedia` label generated and AI-edited content.
- **MakerNote**: a vendor-specific block that every camera and phone writes into EXIF and that editors drop.
- **PCE, peak-to-correlation energy**: the score of a PRNU match, how sharply the correlation peaks at zero shift; 60 or more reads as the same camera here.
- **PRNU, photo-response non-uniformity**: the fixed pattern of pixel sensitivities of one sensor, a fingerprint of one physical camera.
- **Quantization tables**: the step sizes a JPEG encoder used for each frequency; they identify the encoder and, for libjpeg, the exact quality.
- **Resampling**: computing a picture's pixels anew on a different grid, as resizing and turning do. The interpolation leaves a periodic pattern that `resample` measures.
- **Residual**: what is left of a picture after its content is predicted away; noise and interpolation traces live in it.
- **XMP**: Adobe's XML metadata packet, where editors keep history, settings and IDs.
