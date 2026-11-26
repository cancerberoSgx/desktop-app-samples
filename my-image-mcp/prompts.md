
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





---
# future

what about adding other powerful image manipulation tools such as imagemagick ? 

real-time imave viewer to see the changes live

web mcp