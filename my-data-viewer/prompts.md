

# my data viewer

using wxwidgets-test1 project as a template, create a new project using the same technologies and layout called "my-data-viewer"

This project will allow users to create datasources/connections to known databases such as postgres or mysql. On each it will list the tables, columns, indexes and data table, and even create and execute sql scripts over them

It will use sqlite to store all this data in current's user $HOME/.my-data-viewer folder. Will allow to change schema by adding .sql migration files. For each concept there will be a ConceptRepository class which access db using pure sql

For now implement the concept of "datasource" with the following fields:


 * id int PK
 * name text not null
 * type (postgres, mysql, csv) text not null
 * file_path text default null
 * db_host text default null
 * db_port int default null
 * db_name text default null
 * db_user text default null
 * db_password text default null

create a datasourceRepository which allows to 
 * create a new datasource
 * list datasource, filtering by name contains and type
 * delete datasource
 * update datasource


The datasourceRepository will also allow to execute operations: 
 * list all tables
 * for each table list all of its fields (columns), field types, indexes
 * being able to execute sql against it.



In the UI, add a "datasources" screen which allows to CRUD datasource. 

For now, let's just implement the connection type "csv" for which file_path is required which is the path to a csv file
in csv datasources, implement the following operations with this behavior:
 * list all tables : just list one table wich is the csv file name
 * for each table list all of its fields (columns), field types, indexes: just list the csv columns names with "text" type
 * being able to execute sql against it. Use duckdb to load the csv


ANSWER:

Using wxwidgets-test1 project as a template, create a new project using the same
technologies and layout called "my-data-viewer".

This project will allow users to create datasources/connections to known databases
such as postgres or mysql. On each it will list the tables, columns, indexes and data
table, and even create and execute sql scripts over them.

It will use sqlite to store all this data in current user's $HOME/.my-data-viewer
folder. Will allow to change schema by adding .sql migration files. For each concept
there will be a ConceptRepository class which accesses the db using pure sql.

For now implement the concept of "datasource" with the following fields: id, name,
type (postgres, mysql, csv), file_path, db_host, db_port, db_name, db_user,
db_password.

Create a DatasourceRepository which allows to create/list (filter by name-contains
and type)/delete/update a datasource, and to execute operations against it: list all
tables, for each table list its fields/types/indexes, and execute arbitrary sql.

In the UI, add a "Datasources" screen which allows to CRUD datasources.

For now, only the "csv" connection type is implemented: file_path is required, and
its operations behave as: list all tables -> just the csv file name; list columns
-> the csv column names, all typed "text"; execute sql -> query it via DuckDB.





---

# building mydataviewer exec issues

i'm having trouble executing dataviewer executable /home/sg/sources/desktop-apps-samples/my-data-viewer

I've tried to build it using these commands but executables won't work

pyinstaller --noconfirm --windowed --name mydataviewer main.py

pyinstaller --noconfirm mydataviewer.spec

the error I'm having is:

executing wxwidgets-test1/build/wxdemo/mydataviewer errs with:

[PYI-1643785:ERROR] Failed to load Python shared library '/home/sg/sources/desktop-apps-samples/my-data-viewer/build/mydataviewer/_internal/libpython3.14.so.1.0': /home/sg/sources/desktop-apps-samples/my-data-viewer/build/mydataviewer/_internal/libpython3.14.so.1.0: cannot open shared object file: No such file or directory


executing my-data-viewer/dist/mydataviewer/mydataviewer gives error:
Traceback (most recent call last):
  File "main.py", line 3, in <module>
  File "pyimod02_importers.py", line 457, in exec_module
  File "app/frame.py", line 3, in <module>
  File "pyimod02_importers.py", line 457, in exec_module
  File "app/datasources_page.py", line 5, in <module>
  File "pyimod02_importers.py", line 457, in exec_module
  File "app/repositories.py", line 4, in <module>
  File "pyimod02_importers.py", line 457, in exec_module
  File "app/drivers.py", line 4, in <module>
ModuleNotFoundError: No module named 'duckdb'
[PYI-1646256:ERROR] Failed to execute script 'main' due to unhandled exception!


can you help diagnosting and fixing the error?
---


# better UI
 * in my-data-viewer/ unify current sidebar options "About" and "Home" into a single one  "About" which is the last one



# profiles

context:
in my-data-viewer
end users can pick a profile, a profile contains all the funcitonlaity like its own datasources, scripts, etc (all the concepts "belong" to a profile).  At the beggining if there are no profiles in DB, user is asked to create a profile by giving a name.
All their connections, views, queries, etc are associated to a profile

tasks:
 * in db: create profiles db table id, name, createdAt, updatedAt
 * in db datasources table now have a FK to profiles
 * create profileRepository with CRUD operations
 * Create a settings DB table to store the current "profile" so when the app opens the last profile is automatically loaded.
 * in the UI if there's no profile, automatically create a new profile named "default" and use that one
 * in the UI, at the top of the sidebar add a "profiles" option, which will show a "profiles" screen which, list all profiles, allow to delete, update and create new profiles. Also allow to "activate" a profile, in which case the app will only display connections of that profile.




# my-data-viewer-build github actions

add .github/workflows/my-data-viewer-build.yml so I can build my-data-viewer binaries. Use the existing .github/workflows/wxwidgets-test1-build.yml as a template since it's working fine





FUTURE
p2
 * in my-data-viewer datasources table have an action column with an icon button "connect"
 * when clicked the main datasource-inspector screen 