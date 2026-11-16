# Lighting

```bash
exif-inspect light photo.jpg                                              # every usable outline
exif-inspect light --box 120,80,260,300 --box 400,90,520,310 photo.jpg    # one object per box
```

Along the outline where an object hides what is behind it, the surface turns away from the camera, so its normal lies in the image plane and points straight out of the outline. Under one distant light and a matte surface, the brightness just inside the outline is a linear function of that normal, `I = R (N . L) + A`, with `L` the light direction projected onto the picture (Johnson and Farid, 2005). A least-squares fit along one outline gives `L` for that object. Everything in a real photograph shares one light, so an object whose outline says otherwise was lit somewhere else.

## How outlines are chosen

Edge pixels (Sobel gradient, thinned, hysteresis thresholds) are grouped into chains; each chain's normals are turned to point away from its centre, the brightness 2.5 to 4.5 pixels inside is sampled, and the fit is kept when the chain covers at least 100 degrees of normal directions, the model explains at least 70% of the brightness variation, the shading swings by at least 10 grey levels and the direction is determined within 20 degrees. Directions are in degrees: 0 for light from the right, 90 from above, 180 from the left, 270 from below.

- **Without boxes**, every usable outline counts. When outlines carrying at least 60% of the evidence agree within 35 degrees, that is the scene's light, and a well-determined outline more than 35 degrees off it is flagged. When they do not agree, the tool says so and flags nothing.
- **With `--box`**, each box stands for one object and the best outline inside it gives that object's direction. This is the mode for a real question such as whether two people in one picture were lit alike.

The output picture draws each used outline with an arrow toward its light, green when it agrees, red when it does not, and a yellow arrow in the corner for the common direction.

## What to expect

On rendered matte spheres the estimate is within 2 degrees over 12 directions and three backgrounds; a sphere lit from 220 degrees among three lit from 40 is flagged, and one only 60 degrees off as well. Four spheres each lit from a different side give no consensus and no flag.

Real photographs are harder, and the limit is choosing outlines, not the fit. In the scikit-image sample photos the automatic pass kept 6 to 9 outlines per picture, but most belonged to helmet rims, folds, lips and edges between two materials rather than to the silhouette of a convex object, and their directions scattered: the astronaut, the cat and the motorcycle gave no consensus, which is the right answer for that evidence. The method's authors chose outlines by hand; `--box` is the equivalent. Several lights, a nearby light, shiny surfaces, cast shadows and a change of material along the outline all break the model.
