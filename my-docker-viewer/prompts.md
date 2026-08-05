# my-docker-viewer

create a new project "my-docker-viewer" in folder "./my-docker-viewer". This is a desktop project to admin docker containers, images, stats, purges, etc. User the project my-redis-viewer (wxwindows, python) as a template. But don't copy any features, not even profiles or datasources.
Instead use docker CLI to build the following features in separate screens (sidebar). Assume docker is installed in current system and execute all docker related operations using docker cli or fail if not installed.
 * containers:     list all docker containers and their status, dates, names, etc
    * be able to know how much disk and memory and cpu each docker is taking
    *  be able to stop or rm each container
    * filter containers by name, image name, status, etc
 * .github/workflows/my-redis-viewer-build.yml - add a similar my-docker-viewer.yml for this project with similar builds.

You will still use sqlite and migrations as in my-redis-viewer to store user settings in the future.
