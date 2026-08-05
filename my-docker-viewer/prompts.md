# my-docker-viewer

create a new project "my-docker-viewer" in folder "./my-docker-viewer". This is a desktop project to admin docker containers, images, stats, purges, etc. User the project my-redis-viewer (wxwindows, python) as a template. But don't copy any features, not even profiles or datasources.
Instead use docker CLI to build the following features in separate screens (sidebar). Assume docker is installed in current system and execute all docker related operations using docker cli or fail if not installed.
 * containers:     list all docker containers and their status, dates, names, etc
    * be able to know how much disk and memory and cpu each docker is taking
    *  be able to stop or rm each container
    * filter containers by name, image name, status, etc
 * .github/workflows/my-redis-viewer-build.yml - add a similar my-docker-viewer.yml for this project with similar builds.

You will still use sqlite and migrations as in my-redis-viewer to store user settings in the future.


# loading

when the app starts, the "containers" table is empty for some seconds (until docker information retrieval finishes). Add a "loading" visual feedback when this happens or when it's refreshed.

# sort columns

in "containers" table, user can sort all columns by clicking the column header asc and desc.
the "created" column must be friendlier, for example, X hours ago, x days ago, etc instead of a timestamp.

# auto-refresh
currently the application auto-refresh every N seconds. Make a change so the user can activate auto-refresh every N seconds with a checkbox/toggle button. By default auto-refresh is disable (user must manually click the refresh button)

# remove bug
when a container is removed or stopped, the UI must be updated instantaneusly if the docker command runs successfully. (update the table without a full refresh, just virtually)