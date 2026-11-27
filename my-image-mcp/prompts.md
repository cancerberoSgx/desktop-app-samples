
# opencv mcp

I want to implement a project,  a headdless mcp server with all opencv image utilities, from drawing, effects, detection, ml, etc. 
basically allow a llm like claude-code to connect  this mcp server to load images and apply various operations in order.

a workflow could be just using the mcp tools (opencv operations) or a combination of them, like some image analysis task taken by the llm, and other operations by opencv.

do you understand the concept ? 

In the future we could add a visual UI to see the changes in an image, and a web mcp server, but for now keep it local and headless

do you have any questions or suggestions ? I want it to be in python, what's your recommendation to integrate opencv and being able to update its version?

answers:
1. for now  only local files
2. for now just cpu
3. in local folder my-image-mcp (new project). We want to also provide github workflow for building it with CI on multiple platforms, is that possible?
4. I want to be flexible and support all opencv tools that make sense
5. yes let's start with tools for all those, but no more than 10 for each module

also, we want to be generic enough to support other MCP consumers such as openai
can you start now ? 


# build commands
add a how can I build this app locally (in linux) section in readme


when I've executed this with claude-code works but claude said : 

Something I noticed about my-image-mcp: every drawing tool returns a new image and leaves its input unchanged, so drawing calls on the same image can't run in parallel. When I drew two ellipses at once, each landed on a separate copy and neither copy had both. I redrew one ellipse in order, and the saved file has all five shapes. To do several drawings in one call, run_pipeline is the safer option.

is this something we should take care about ? maybe by default working on the same image but also yesallowing current behavior with a flag ? what do you think ? 

---

# imagemagick
I also want to add imagemagick tools since I know it has some features that opencv has not. Do you think is a good idea ? can you list IM operations that opencv don't have ? what would be the best way of integrating imagemagick ? 


A:

Yes, I think it's a good idea, as long as it's a curated, optional module and not a passthrough to the magick command. ImageMagick 7.1.2 is installed on this machine. I also checked that libvips is installed, which matters for one of the options below.

What ImageMagick does that OpenCV doesn't, or does poorly

┌───────────────┬────────────────────────────────────────────────────────────────────────────┬────────────────────────────────────────────────────┐
│     Area      │                                ImageMagick                                 │                    OpenCV today                    │
├───────────────┼────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────┤
│ Text          │ TrueType/OpenType fonts, Unicode, word-wrapped captions, stroke, shadow,   │ Hershey vector fonts only, ASCII only. This is the │
│               │ gravity placement                                                          │  biggest gap.                                      │
├───────────────┼────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────┤
│ Formats       │ HEIC, AVIF, JPEG XL, PSD layers, PDF/SVG/EPS rasterization (with a density │ Common raster formats only                         │
│               │  setting), animated GIF/WebP frames, camera RAW (through helper libraries) │                                                    │
├───────────────┼────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────┤
│ Color         │ ICC profile conversion, embedded profiles, processing in linear light      │ None. Everything is treated as plain sRGB numbers. │
│ management    │                                                                            │                                                    │
├───────────────┼────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────┤
│ Metadata      │ EXIF/IPTC/XMP reading, -auto-orient, -strip                                │ Ignores orientation and metadata                   │
├───────────────┼────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────┤
│ Blend modes   │ About 60 Photoshop-style modes (multiply, screen, overlay, soft light,     │ Weighted add and bitwise ops only                  │
│               │ color dodge…)                                                              │                                                    │
├───────────────┼────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────┤
│ Color grading │ -level, -auto-level, -auto-gamma, -sigmoidal-contrast, -modulate,          │ Basic per-pixel math only                          │
│               │ -clut/-hald-clut (LUTs), -tint, -sepia-tone, -colorize                     │                                                    │
├───────────────┼────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────┤
│ Quantization  │ -colors N / -remap palette.png with Floyd–Steinberg or ordered dithering   │ k-means without dithering (what I just added)      │
├───────────────┼────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────┤
│ Distortions   │ -distort barrel (lens correction), arc, polar, Shepards (control-point     │ Affine, perspective, remap only                    │
│               │ warping), -liquid-rescale (seam carving)                                   │                                                    │
├───────────────┼────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────┤
│ Effects       │ charcoal, sketch, oil paint, swirl, implode, wave, vignette, polaroid,     │ A few in contrib (stylization, pencilSketch,       │
│               │ Kuwahara, shadow                                                           │ xphoto.oilPainting), reachable through cv_call     │
├───────────────┼────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────┤
│ Layout        │ montage contact sheets, borders and frames, -trim with a fuzz tolerance,   │ Manual only                                        │
│               │ gradients                                                                  │                                                    │
├───────────────┼────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────┤
│ Comparison    │ compare -metric SSIM, PSNR, AE and more, plus a visual diff image          │ PSNR only (cv2.PSNR)                               │
└───────────────┴────────────────────────────────────────────────────────────────────────────┴────────────────────────────────────────────────────┘

Things it doesn't add: edges, contours, features, detection and segmentation are much better in OpenCV. I'd leave IM's versions of those out.

How I'd integrate it

Use the magick command-line tool through a subprocess, in a new optional module (max 10 tools), registered only if magick is on PATH.

- Data flow: keep numpy arrays as the handles. Pipe each image to magick png:- <ops> png:- through stdin and stdout, with 16-bit PNG, or TIFF for float data. Nothing touches disk, the store, handles, history and export_pipeline all keep working, and immutability holds automatically.
- Optional dependency: IM isn't something pip can install, and today the wheel works everywhere without system libraries. So the tools appear only when IM is detected, opencv_info reports whether it's available, and tests skip when it's missing. CI would install it with apt or brew (Windows is optional).
- Security matters more than usual. IM has a history of serious vulnerabilities (ImageTragick), and it can read files and URLs on its own: @file, url:, msl:, text:, ephemeral:. Going through it would bypass your path sandbox. So:
  - curated tools with validated, enum-style parameters only, and no "raw args" tool;
  - a strict policy.xml shipped with the package and pointed to by MAGICK_CONFIGURE_PATH, which disables URL, MSL, MVG, TEXT, EPHEMERAL and @-indirect reads, and enables PDF/PS only for a dedicated, path-checked loader;
  - -limit memory/time settings and a subprocess timeout.
- Why not Wand (the Python binding)? It still needs the system library, and it runs IM inside the server process, so an IM crash or memory blow-up takes the MCP server down with it. With the subprocess approach, the server survives and gets a clear error.

Alternative worth considering: a lot of the table is also covered by pyvips[binary], which installs from pip with libvips bundled on Linux, macOS and Windows. That covers text with real fonts, HEIC/AVIF/JPEG XL/PDF/SVG loading, ICC conversion, blend modes, high-quality resizing and auto-orient, and it's fast and memory-efficient. It doesn't cover the artistic effects, seam carving, montage, and -distort barrel/Shepards. Unlike IM, it wouldn't break the "pip install and it works" property.

What I'd do

1. Add pyvips as an optional dependency for formats, color management, text and blend modes. It's pip-installable and closes most of the practical gaps.
2. Add an optional magick module (subprocess plus a strict policy) for what only IM has, about 10 tools: magick_text (captions), blend_mode, color_grade (level/modulate/sigmoidal/auto), apply_lut (Hald CLUT), quantize (dithering/remap), distort (barrel/arc/polar/Shepards), liquid_rescale, artistic_effect (charcoal/sketch/oil/swirl/vignette…, one enum), montage, compare_images (SSIM and visual diff).

If you'd rather have just one new dependency, use IM alone with the module above plus a magick_load tool for the extra formats, and accept that it's a system dependency. Do you want both (pyvips and IM), or IM only?






# tools docs
enumerate and well describe each supported tool in a docs/tools.html document - so end users humans can understand what's supported. Tag each tool so they can be filterable by funcionality effects, drawing, layout, metadata, etc. Add an index and a search & filters (100% offline)


---
# test 1

cost savings and viability

i want to know if implementing some image operatoins using my-image-mcp vs no using the mcp saves costs or not. 
also I want to know if one of the following tasks is not possible today using only mcp: 


 * remove background 
 * detect faces and switching them
 * remove hair
 *  switch pepole faces
 * animated gif 
 * switch morph people faces in animated gif or video

 p2 ok, so for image 
<>


---
# future

animations & videos output


what about adding other powerful image manipulation tools such as imagemagick ? 

# real-time viewer
one or more images are generated when llm uses this mcp, 
and I would like to see them in real time in an UI, not only the resulting one but also the intermediate images

# web mcp

# parent docs

# videos input