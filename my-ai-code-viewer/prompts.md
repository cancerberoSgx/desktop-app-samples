
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


# per projects used tokens and cost columns calculation

# copy conversation 
in the conversation details, user is able to copy conversation both full including agents/reasoning or simple (default) , only question and answer
