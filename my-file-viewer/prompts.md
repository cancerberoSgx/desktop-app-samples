
# my-file-viewer
create a new project my-file-viewer in folder "./my-file-viewer" . Use ./my-redis-viewer project as a skeleton and copy all its features as sqlite db and migrations, preferences, .github/workflows/my-redis-viewer-build.yml . Dont' model profiles or datasources,
This new project is a performant file explorer. Model favorites table which are folders added as favorite folder list. Favorites are displayed in the left sidebar, which is collapsible (store in preferences)
User selects a folder and see its content (add the last selected folder to user preferences and remember it)
At the right there's is the folder contents explorer which list all files and folders inside the selected folder. The list of folder content is a table displaying file name, size, modified date. User can sort by these columns
in the future we'll add more columns, file details, file types, complex glob selection, folder recursive size, etc
There must be a "service" with folder actions and all the actions need to happen async in order not to block/freeze the UI as my-redis-viewer does. This pattern must be clear when we add other features in the future

# folder tree
in the folder explorer page, folders are displayed as a tree. User can "expand" a folder recursively. For performance only query folders content when user "expand it"
p2
user can expand a folder by pressing "space". User can open folders or files by pressing enter. 
there's an icon button at the right of the file path to collapse all expanded folders
p2
issue, tjhi shappen only in folder /home/sg but not in /home. In /home/sg, folders are not expandable as trees but files are.

# settings
File-> settings menu take the user to settings modal. Currently this modal allow users to set the following settings:
 * display hidden files (by default, hidden files and folders are not shown)

# multiple select
there's always one or more files selected. User now can select next file using up and down keys or pag and end/beggining keys, which is OK
Now users are able to select multiple files. Implement the following ways of multiple file selection
 * entering shift+up or down or prev-pag, or end/begining will allow to select other files in range. 
 * clicking a file or pressing up, down, prev-pag, end, etc keys without shift will do single select
At the bottom bar, right , there's a "selected: N" 

# remember last location
current folder is remembered next time user open's the app

# copy current path
at the right of current folder path (breadcrumb) add a copy to clipboard button that copies the current file path
if user pastes (menu edit paste or ctrl-v) a string which is a valid path, the current path (breadcrumb) transforms temporarily into a text input with that path, focused, and if user press ENTER it will navigate to given folder path and if the final path is to a file the file will be selected. After pressing enter the breadcrumb returns to its normal behavior
p2
there's a menu Edit->"Copy paths" which will copy the absolute path to all selectged files and folders to clipboard, or by pressing ctrl-c


# actions
For any selected file or files, there are actions available
For single file selection these actions are available: delete (supr), rename (f2), open (enter)
For multiple selection these actions are available: delete
When user right click on a selected file or files, the selection don't go away and a context menu is shown with actions.
Delete needs confirmation can be configured in settings
Also all these actions are available in File menu
if an action is not applicable (because of selection) they appear as disabled
p2
when I delete or rename a file, the vertical scroll changes and it shouldn't , the current vertical user scroll must remain the same


