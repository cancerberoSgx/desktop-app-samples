
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

# collapse actions
in conversation details, instead of having actions buttons, move them to a single dropdown / menu "actions". 
Also add a "Conversation" menu where user have this actions too


#  stats report
As a user that uses claude-code, opencode a lot I would like to see stats regarding of tools, projects and conversations in a friendly UI

ideas: 
 * how much i use different agents (claude, open, etc)
 * which are my top projects in terms of money, tokens, conversations, etc
 * conversation stats such as number of q & A, numbers of reasoning, etc breakdown like
I expect some charts and data tables for each

Do you understand motivation?, do you have any more stats ideas that can be interesting of ia tools users ? do you have any design suggestions ? 



# theme
i want to add dark/light theme for the app - in preferences -> visual -> theme - but first a question. Do you know if is it possible to read the system's preference for dark / light desktop theme in the current project ? so we can consider that also in our theme functionality

# docs
add documentation for this project in ../docs/my-ai-code-viewer like the rest of projects have. You can leave screenshots placeholders for me to generate

# outline ui
look how the outline view (toc) looks like now (very ugly). Please correct the colors and set the same small fontsize for both q&a. Also use similar colors gray1 and gray2 

# projects sorting

in the projects list, user can sort by projects, conversation and last activity 


# projects cosst and tokens
in the projects list, add columns for cost and tokens (sum of each conversation). these two columns are also sortable

# export project

Currently users are able to export one conversation. We want to implement them to be able to export all or some conversations of a project. By clicking menu Projects->Export conversations as HTML... a dialog is shown where user can select 1 or more conversations of that project (by default all are selected). The html export format is the same as single conversation HTMl export, but with a left side bar to select the conversation to see.  Any questions?

---

# future

codex
can we also implement codex to see projects and conversations that it use ? do you know how to do it ? do you need me to create some codex threads to test before ? 


# tags
being able to tag a conversatoin or a particular question or answer in a conversation. Objective: after reading, I can tag something as important or "performance" and quickly locate it in the future.