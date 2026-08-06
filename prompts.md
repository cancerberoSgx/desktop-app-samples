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

# my-disk-viewer

create a new project "my-disk-viewer" in folder "./my-disk-viewer". This is a desktop project to visualize disk usage in folders recursively and the objective is an user to realize which folders or file types are the ones using most disk space so they can remove files to free space. 

Use the project my-docker-viewer (wxwindows, python) as a template. But don't copy any features, not even profiles or datasources.
instead, first research which existing tools can be use to calculate per-file disk usage like unix cmd 'du' or other alternatives for windows (we can skip windows for now, but we want to implelment the same there.)
for linux and macos use du or research for better alternatives
We want to use sqlite & migrations to store the disk usage stats for each folder and file and for folders the total disk space used by its content. Use sqlite as a cache and users will be able to "reload" a folder stats recursively.
it's important to follow my-docker-viewer async cli cmds execution patterns to now block UI while long run commands are being executed.
Let me know your thoughts first and ask me questions about the user experience 
Also we would like to have a table view and a chart / piechart style view when analything a folder's recursive disk usage
let me know a plan to implement this UI
