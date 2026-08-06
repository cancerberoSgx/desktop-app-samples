
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

p2
ok , proceed now creating the UI
There should

p3 
now implement a new tab that dislpay  a piechart with legends to better visualize subfolers and subfiles disk size distribution

# binaries
implement a github actions file like ../.github/workflows/my-docker-viewer-build.yml which builds this project my-disk-driver to each platform linux, macos, windows.