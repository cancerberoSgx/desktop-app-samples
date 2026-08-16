
# my-file-viewer
create a new project my-file-viewer in folder "./my-file-viewer" . Use ./my-redis-viewer project as a skeleton and copy all its features as sqlite db and migrations, preferences, .github/workflows/my-redis-viewer-build.yml . Dont' model profiles or datasources,
This new project is a performant file explorer. Model favorites table which are folders added as favorite folder list. Favorites are displayed in the left sidebar, which is collapsible (store in preferences)
User selects a folder and see its content (add the last selected folder to user preferences and remember it)
At the right there's is the folder contents explorer which list all files and folders inside the selected folder. The list of folder content is a table displaying file name, size, modified date. User can sort by these columns
in the future we'll add more columns, file details, file types, complex glob selection, folder recursive size, etc
There must be a "service" with folder actions and all the actions need to happen async in order not to block/freeze the UI as my-redis-viewer does. This pattern must be clear when we add other features in the future
