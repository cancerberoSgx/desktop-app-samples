
# code ai analyzer


create a new project my-ai-code-viewer in folder ".my-ai-code-viewer" . Use ./my-redis-viewer project as a skeleton and design and copy all its features as sqlite db and migrations, preferences, .github/workflows/my-redis-viewer-build.yml . Dont' model profiles or datasources or anything else. 

Application requirements:

I want to have an application that displays all my claude-code and open-code projects and for each project analyze each conversations/threads I had.
Let's start with claude-code but design generic to also support other cli tools such as open-code
The app should display all folder names (projects) and if I click one display all the conversations. 
If I click the conversation I should be able to access its content
do you have any questions or UI design suggestions or blockers before start ? 

answers:
1. yes
2. yes
3. ok
4. in some future I would like to have the ability to search for something in multiple projects
5. will follow something you recommend here.
6. ok nowhting else or now


# response as json

when a conversation is displayed, if user press "EXC" the conversation is closed so he can see all conversatoins table

context: both claude and open code chat response / answer texts is markdown but is not rendered as markdown but as plain text
task: add a setting response-as-markdown toggle
do you have any questions before start ?

---
# future

# conversations sorting and filtering
in the conversatoins list:
    I can filter conversations by title (contains words)
    I can sort conversatoins by prompt, tool call, tokens and cost
When the conversation details screen is open, if I press ESC key it't closed so I can see the conversation list table with full width.
do you have any questions before start?

# conversation details TOC
in the conversation details, there are many questions, each question with a sub-assistant and answer. Currently there's no way of see a sumary of all the conversatoin. I would like to see something like a table of contents with an item for each question and its answer. When clicking the table of contents items it shuold take me to the scrolling position of that question or answer
Do you see any better way of summarizing the content of a conversation with many q&a another way than a toc ? do you have any questions or alterinative proposals for accomplishing the goal before writing code ? 

side bar 
delete the sidebar. 
put the preferences in a file->preferences dialog
put the "About contents" in a help->about dialog



# per projects used tokens and cost columns calculation

# copy conversation 
in the conversation details, user is able to copy conversation both full including agents/reasoning or simple (default) , only question and answer

# remember window size
when users resize the app window or  maximize , this should be rememberd in settings so next time they open the app is of that size

# conversation settings

all the conversation options like outline, q&a only, etc must be also configurable in file->settings, "conversation" section or tab
the default conversation settings are: 
q&a only = true
render markdown = true
and the rest in false

# export conversation as html
user can export (save as) a conversation into good looking and interactive static html
The html have options to set/unset each setting
do you have any questions ? 

# future

# collapse actions
in conversation details, instead of having actions buttons, move them to a single dropdown / menu "actions". 
Also add a "Conversation" menu where user have this actions too

# theme
dark/light theme for the app - in preferences -> visual -> theme

# docs
../docs/my-ai-code-viewer page

# conversation stats
stats charts of conversation length (qa), number of conversations per project

question: explain how this project saves / update the data into mysqlite db, is it at startup ? can we be  sure that when the app starts all the project conversations are updated or when that happens