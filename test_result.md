#====================================================================================================
# START - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================

# THIS SECTION CONTAINS CRITICAL TESTING INSTRUCTIONS FOR BOTH AGENTS
# BOTH MAIN_AGENT AND TESTING_AGENT MUST PRESERVE THIS ENTIRE BLOCK

# Communication Protocol:
# If the `testing_agent` is available, main agent should delegate all testing tasks to it.
#
# You have access to a file called `test_result.md`. This file contains the complete testing state
# and history, and is the primary means of communication between main and the testing agent.
#
# Main and testing agents must follow this exact format to maintain testing data. 
# The testing data must be entered in yaml format Below is the data structure:
# 
## user_problem_statement: {problem_statement}
## backend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.py"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## frontend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.js"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## metadata:
##   created_by: "main_agent"
##   version: "1.0"
##   test_sequence: 0
##   run_ui: false
##
## test_plan:
##   current_focus:
##     - "Task name 1"
##     - "Task name 2"
##   stuck_tasks:
##     - "Task name with persistent issues"
##   test_all: false
##   test_priority: "high_first"  # or "sequential" or "stuck_first"
##
## agent_communication:
##     -agent: "main"  # or "testing" or "user"
##     -message: "Communication message between agents"

# Protocol Guidelines for Main agent
#
# 1. Update Test Result File Before Testing:
#    - Main agent must always update the `test_result.md` file before calling the testing agent
#    - Add implementation details to the status_history
#    - Set `needs_retesting` to true for tasks that need testing
#    - Update the `test_plan` section to guide testing priorities
#    - Add a message to `agent_communication` explaining what you've done
#
# 2. Incorporate User Feedback:
#    - When a user provides feedback that something is or isn't working, add this information to the relevant task's status_history
#    - Update the working status based on user feedback
#    - If a user reports an issue with a task that was marked as working, increment the stuck_count
#    - Whenever user reports issue in the app, if we have testing agent and task_result.md file so find the appropriate task for that and append in status_history of that task to contain the user concern and problem as well 
#
# 3. Track Stuck Tasks:
#    - Monitor which tasks have high stuck_count values or where you are fixing same issue again and again, analyze that when you read task_result.md
#    - For persistent issues, use websearch tool to find solutions
#    - Pay special attention to tasks in the stuck_tasks list
#    - When you fix an issue with a stuck task, don't reset the stuck_count until the testing agent confirms it's working
#
# 4. Provide Context to Testing Agent:
#    - When calling the testing agent, provide clear instructions about:
#      - Which tasks need testing (reference the test_plan)
#      - Any authentication details or configuration needed
#      - Specific test scenarios to focus on
#      - Any known issues or edge cases to verify
#
# 5. Call the testing agent with specific instructions referring to test_result.md
#
# IMPORTANT: Main agent must ALWAYS update test_result.md BEFORE calling the testing agent, as it relies on this file to understand what to test next.

#====================================================================================================
# END - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================



#====================================================================================================
# Testing Data - Main Agent and testing sub agent both should log testing data below this section
#====================================================================================================
user_problem_statement: "BookLoop — local book-exchange social marketplace. Iteration 3: Genre Match (Discover top-matches strip + shared-genre highlights), Book Wishlist (soft preferences: genres/languages/reading interests → nearby books & readers; owner demand nudges), Swap Streak badges (First Loop 1 / Loop Regular 3 / Bookworm 5 / Loop Legend 10)."

backend:
  - task: "Genre match scoring in GET /api/discover/people (shared_genres, match_score, top_matches) and GET /api/users/{id}"
    implemented: true
    working: "NA"
    file: "backend/server.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
      - working: "NA"
        agent: "main"
        comment: "match_info() combines profile+shelf genres, languages, reading_interests; people sorted matches-first."
  - task: "Wishlist GET /api/wishlist (preferences, books, people, needs_setup) + PUT /api/users/me reading_interests"
    implemented: true
    working: "NA"
    file: "backend/server.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
      - working: "NA"
        agent: "main"
        comment: "Soft matching only (genre/language/distance/exchanging). No exact-title matching by design."
  - task: "Demand nudges GET /api/books/demand + wanted_by in GET /api/books/detail/{id}"
    implemented: true
    working: "NA"
    file: "backend/server.py"
    stuck_count: 0
    priority: "medium"
    needs_retesting: true
    status_history:
      - working: "NA"
        agent: "main"
        comment: "Counts nearby (<=25km) exchanging readers whose genres include the book genre (and language)."
  - task: "Badges in public_user + new_badges on POST /api/swaps/{id}/complete + system message"
    implemented: true
    working: "NA"
    file: "backend/server.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
      - working: "NA"
        agent: "main"
        comment: "badges: [{id,label,threshold,blurb,earned}]. Unlock detected when both parties complete."

frontend:
  - task: "Discover Great-matches strip + PersonCard shared genre highlight + badge pill"
    implemented: true
    working: "NA"
    file: "frontend/app/(tabs)/discover.tsx, frontend/src/components/cards.tsx"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
      - working: "NA"
        agent: "main"
        comment: "testIDs: genre-matches, match-card-{id}, matches-setup, shared-genres-pill, person-badge-{id}"
  - task: "Wishlist screen /wishlist (preferences editor + books/readers for you) reachable from Profile wishlist-card"
    implemented: true
    working: "NA"
    file: "frontend/app/wishlist.tsx, frontend/app/(tabs)/profile.tsx"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
      - working: "NA"
        agent: "main"
        comment: "testIDs: wishlist-card, toggle-preferences, wish-genre-*, wish-lang-*, wish-interest-*, save-wishlist, wish-person-{id}"
  - task: "Badges on Profile (BadgeGrid, badge-progress) and person profile (badge-chip-*)"
    implemented: true
    working: "NA"
    file: "frontend/src/components/badges.tsx"
    stuck_count: 0
    priority: "medium"
    needs_retesting: true
    status_history:
      - working: "NA"
        agent: "main"
        comment: ""
  - task: "My Books demand banner + wanted pills; Book detail wanted-by; swap complete badge toast"
    implemented: true
    working: "NA"
    file: "frontend/app/(tabs)/books.tsx, frontend/app/book/[id].tsx, frontend/app/swap/[id].tsx"
    stuck_count: 0
    priority: "medium"
    needs_retesting: true
    status_history:
      - working: "NA"
        agent: "main"
        comment: "testIDs: demand-banner, wanted-pill-{bookId}, wanted-by"

metadata:
  created_by: "main_agent"
  version: "3.0"
  test_sequence: 3
  run_ui: true

test_plan:
  current_focus:
    - "Genre match scoring"
    - "Wishlist endpoint + screen"
    - "Badges + swap complete unlock"
    - "Demand nudges"
  stuck_tasks: []
  test_all: false
  test_priority: "high_first"

agent_communication:
  - agent: "main"
    message: "Iteration 3 implemented. Backend verified manually via python requests (login test@bookloop.com/test1234). Frontend smoke-tested on web preview. Please run backend API tests + frontend E2E for the new features."
