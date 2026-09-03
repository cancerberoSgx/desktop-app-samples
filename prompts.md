# github actions to build binaries.

I've just commited this parent folder as a github repository. Can you add a configuration for gitactions that builds the binaries for wxwidgets-test1 for platforms linux, windows and macos ? Is it possible ? 

make sure .github/workflows/wxwidgets-test1-build.yml doesn't run automatically, and only explicitly pressing a button, is that possible ? 



# pages / website
This folder contains several desktop app projects using wxwindows & python. They work and are useful and I plan to add more 
they compile successfully in gitlab actions .github/workflows to executables for linux, macos and windows.
I would like now to create homepages for each of them, goodlooking and with screenshots or recordings of their features since I think they are cool and lightweight (have advantage over similar opensource tools.)
Please analyze and recommend an approach to create websites for each app using github pages maybe, and if you can elaborate all this material yourself or you need me to perform some screenshots or screen recording. 
I would like that documents generates to be able to be updated both by claude or myself a human.
For now these apps won't have a reference manual or guide, just a single html with screenshots and data and some small instructions & tutorials
Don't write any code, first plan how would you perform this so we have a template / framework in place to document any of the existing a new desktop apps in this folder


# better linux distributions
currently, the github action files located at .github/workflows build a linux bundle which is very heavy both in distribution size and exec memory. I wonder if we can build binaries for arch linux to distribute on AUR which use dynamic link and be lightweight. Can you, without writin code, tell me if that0s possible using pyinstaller or other tools ? 

# missing docs
in docs/index.html we've created some html docs user facing for each docs/my-data-viewer docs/my-redis-viewer project's. Now complete the documentation to also add projects (my-disk-viewer) (my-docker-viewer) and for existing projects make sure all their features are mentioned. Notice that this documentation is for each application homepage, add placeholders for relevant screenshots and I will add them myself later



# version
in all projects (my-data-viewer) (my-disk-viewer) (my-docker-viewer) (my-documents-viewer) (my-file-viewer) (my-redis-viewer), I want to start versioning each with a semver like 1.0.3. Both in gitlab action builds / artifacts and in aur/ code.
How would you recommend to do this ? (don't write code)

p2
convert scripts/bump-app-version.sh to python. Add a section in readme.md about how to version projects
Also add an option --patch that just increment patch version number


also I need some way of knowing the version of  the executable I'm running, like in file Help->about menu. Also I would like to see
  the version in the github artifacts file names

p2
seems to be working fine, so now perform the same you did for my-redis-viewer for the rest of the projects (my-data-viewer) (my-disk-viewer) (my-docker-viewer) (my-documents-viewer) (my-file-viewer)

p3
how can we support versioning in docs/ so links to download points to the current versions ? 

# docs
in /docs, remove any mention to wxPython - just mention "lightweight"
only at the bottom 
---
