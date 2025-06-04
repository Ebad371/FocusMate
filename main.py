import streamlit as st
import pandas as pd
import sqlite3
import json
import datetime
import time
from streamlit_option_menu import option_menu
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time
from streamlit_autorefresh import st_autorefresh
from google import genai
import openai  # Keep this temporarily for other functions
import os
from streamlit_ace import st_ace  # Add this import
from streamlit.components.v1 import html
import extra_streamlit_components as stx

# Replace OpenAI configuration with Gemini
# Get API key from Streamlit secrets
client = genai.Client(api_key="AIzaSyCLO-UWeTPzwfpqv0ijoMSBW2i6pzkS0-U")

def get_cookie_manager():
    return stx.CookieManager()

def update_course_progress(user_id, course_id, current_level):
    """Update course progress and status based on completed levels."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get total number of levels in the course
    cursor.execute('''
    SELECT COUNT(DISTINCT level) 
    FROM challenges 
    WHERE course_id = ?
    ''', (course_id,))
    total_levels = cursor.fetchone()[0]
    
    # Calculate progress percentage
    progress_percentage = (current_level / total_levels) * 100
    
    # Determine status
    status = "In Progress"
    if progress_percentage >= 100:
        status = "Completed"
    
    # Update user_progress
    cursor.execute('''
    UPDATE user_progress 
    SET progress_percentage = ?, 
        status = ?,
        last_accessed = CURRENT_TIMESTAMP
    WHERE user_id = ? AND course_id = ?
    ''', (progress_percentage, status, user_id, course_id))
    
    conn.commit()
    conn.close()

def get_challenge_by_level(course_id, level):
    """Get challenge content for a specific level."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT id, course_id, level, title, description, video_url, quiz_data
    FROM challenges 
    WHERE course_id = ? AND level = ?
    ''', (course_id, level))
    
    challenge = cursor.fetchone()
    conn.close()
    return challenge

def get_next_level(course_id, current_level, difficulty):
    """Get the next appropriate level based on reflection difficulty."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get all available levels for the course
    cursor.execute('''
    SELECT DISTINCT level, quiz_data
    FROM challenges 
    WHERE course_id = ?
    ORDER BY level
    ''', (course_id,))
    
    levels = []
    for row in cursor.fetchall():
        level_data = json.loads(row[1])
        if "coding_exercises" in level_data:
            for exercise in level_data["coding_exercises"]:
                if exercise.get("difficulty"):
                    levels.append((row[0], exercise.get("difficulty")))
                    break
    
    conn.close()
    
    # Sort levels by difficulty
    easy_levels = [l[0] for l in levels if l[1] == "easy"]
    medium_levels = [l[0] for l in levels if l[1] == "medium"]
    hard_levels = [l[0] for l in levels if l[1] == "hard"]
    
    # If user found it hard, move to an easier level if available
    if difficulty == "hard":
        easier_levels = [l for l in easy_levels + medium_levels if l > current_level]
        if easier_levels:
            return min(easier_levels)
    # If user found it easy, move to a harder level if available
    else:
        harder_levels = [l for l in medium_levels + hard_levels if l > current_level]
        if harder_levels:
            return min(harder_levels)
    
    # If no appropriate level found, move to next sequential level
    return current_level + 1

def get_db_connection():
    return sqlite3.connect('focusmate.db')

def evaluate_code_with_gemini(user_code, exercise):
    """Evaluate user's code submission using Gemini."""
    try:
        # First, try to run the code locally with test cases
        test_results = []
        for test in exercise["test_cases"]:
            try:
                # Create a local scope
                local_scope = {}
                # Execute the user's code in the local scope
                exec(user_code, local_scope)
                
                # Get the function name from the first line of starter code
                func_name = exercise["starter_code"].split("def ")[1].split("(")[0]
                
                # Get the function from local scope
                func = local_scope[func_name]
                
                # Run the test case
                result = func(test["input"])
                passed = result == test["expected"]
                test_results.append({
                    "input": test["input"],
                    "expected": test["expected"],
                    "got": result,
                    "passed": passed
                })
            except Exception as e:
                test_results.append({
                    "input": test["input"],
                    "error": str(e)
                })
        
        # Construct the prompt for Gemini
        prompt = f"""
        Code Exercise: {exercise['description']}
        
        User's Code:
        ```python
        {user_code}
        ```
        
        Test Results:
        ```python
        {json.dumps(test_results, indent=2)}
        ```
        
        Please evaluate the code and provide feedback in the following format:
        1. Correctness: [Yes/No/Partial] - Based on test cases
        2. Test Cases: Summarize which tests passed/failed
        3. Code Quality: Evaluate style, efficiency, and best practices
        4. Suggestions: Provide specific improvements if needed
        5. Explanation: Brief explanation of any issues found
        
        Keep the feedback constructive and educational.
        ONLY RETURN THE ABOVE FOR THIS CODE. NOT FOR ANY CODE BEFORE.
        """
        
        # Generate response using Gemini
        response = client.models.generate_content(
            model='gemini-2.5-flash-preview-04-17',
            contents=prompt
        )
        return response.text if response.text else "No response generated"
    except Exception as e:
        return f"Error evaluating code: {str(e)}"

def analyze_reflection_with_gemini(reflection_text):
    """Analyze user's reflection to determine if they found the content easy or hard."""
    try:
        prompt = f"""
        Analyze this student's reflection on a coding exercise and determine if they found it easy or hard.
        Consider words and phrases that indicate difficulty level, understanding, and confidence.
        
        Reflection:
        {reflection_text}
        
        Classify as either 'easy' or 'hard' and explain why in JSON format:
        {{
            "classification": "easy/hard",
            "confidence": 0-1,
            "reasoning": "brief explanation"
        }}
        """
        
        response = client.models.generate_content(
            model='gemini-2.5-flash-preview-04-17',
            contents=prompt
        )
        
        if not response.text:
            return {"classification": "medium", "confidence": 0.5, "reasoning": "Unable to analyze reflection"}
            
        try:
            result = json.loads(response.text)
            # Validate the response format
            if "classification" not in result or result["classification"] not in ["easy", "hard"]:
                return {"classification": "medium", "confidence": 0.5, "reasoning": "Invalid response format"}
            return result
        except json.JSONDecodeError:
            # If response is not valid JSON, do basic text analysis
            text_lower = reflection_text.lower()
            if any(word in text_lower for word in ["difficult", "hard", "confused", "challenging", "stuck"]):
                return {"classification": "hard", "confidence": 0.7, "reasoning": "Based on keywords indicating difficulty"}
            elif any(word in text_lower for word in ["easy", "simple", "clear", "understood", "confident"]):
                return {"classification": "easy", "confidence": 0.7, "reasoning": "Based on keywords indicating ease"}
            return {"classification": "medium", "confidence": 0.5, "reasoning": "Unable to determine difficulty"}
            
    except Exception as e:
        return {"classification": "medium", "confidence": 0.5, "reasoning": str(e)}

def get_next_level_content(course_id, current_difficulty):
    """Get content for the next level based on current difficulty."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get all challenges for the course
    cursor.execute('''
    SELECT id, quiz_data FROM challenges 
    WHERE course_id = ?
    ''', (course_id,))
    challenges = cursor.fetchall()
    
    # Filter challenges based on difficulty
    appropriate_challenges = []
    for challenge in challenges:
        data = json.loads(challenge[1])
        if "coding_exercises" in data:
            for exercise in data["coding_exercises"]:
                if current_difficulty == "hard" and exercise.get("difficulty") == "easy":
                    appropriate_challenges.append(challenge[0])
                elif current_difficulty == "easy" and exercise.get("difficulty") == "hard":
                    appropriate_challenges.append(challenge[0])
    
    conn.close()
    return appropriate_challenges if appropriate_challenges else None

# Add this function after get_next_level_content()
def get_current_challenge(user_id, course_id):
    """Get the current challenge for the user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get user's current challenge
    cursor.execute('''
    SELECT last_challenge_id FROM user_progress 
    WHERE user_id = ? AND course_id = ?
    ''', (user_id, course_id))
    result = cursor.fetchone()
    
    if not result or not result[0]:
        # If no challenge is set, get the first challenge
        cursor.execute('''
        SELECT id FROM challenges 
        WHERE course_id = ? 
        ORDER BY level ASC 
        LIMIT 1
        ''', (course_id,))
        first_challenge = cursor.fetchone()
        if first_challenge:
            # Set this as the user's current challenge
            cursor.execute('''
            UPDATE user_progress 
            SET last_challenge_id = ? 
            WHERE user_id = ? AND course_id = ?
            ''', (first_challenge[0], user_id, course_id))
            conn.commit()
            current_challenge_id = first_challenge[0]
        else:
            current_challenge_id = None
    else:
        current_challenge_id = result[0]
    
    if current_challenge_id:
        # Get challenge details
        cursor.execute('''
        SELECT id, course_id, level, title, description, video_url, quiz_data
        FROM challenges 
        WHERE id = ?
        ''', (current_challenge_id,))
        challenge = cursor.fetchone()
    else:
        challenge = None
    
    conn.close()
    return challenge

# White noise function definition
def show_white_noise_player(key_suffix="", show_controls=False, show_stop=True):
    if show_controls:
        if not st.session_state.white_noise_playing:
            if st.button("▶️ Start White Noise", key=f"start_noise_{key_suffix}", use_container_width=True):
                st.session_state.white_noise_playing = True
                st.rerun()
        elif show_stop:
            if st.button("⏹️ Stop White Noise", key=f"stop_noise_{key_suffix}", use_container_width=True):
                st.session_state.white_noise_playing = False
                st.rerun()
    
    if st.session_state.white_noise_playing:
        # Hidden video player with white noise
        st.markdown(f"""
        <div style="display: none">
        <iframe width="1" height="1" src="https://www.youtube.com/embed/nMfPqeZjc2c?autoplay=1&controls=0" allow="autoplay">
        </iframe>
        </div>
        """, unsafe_allow_html=True)

# Initialize session state for reflections
if "show_reflection" not in st.session_state:
    st.session_state.show_reflection = False
if "current_feedback" not in st.session_state:
    st.session_state.current_feedback = None
if "current_exercise" not in st.session_state:
    st.session_state.current_exercise = None
if "submitted_code" not in st.session_state:
    st.session_state.submitted_code = None
if "reflection_submitted" not in st.session_state:
    st.session_state.reflection_submitted = False
if "reflection_text" not in st.session_state:
    st.session_state.reflection_text = None
if "reflection_analysis" not in st.session_state:
    st.session_state.reflection_analysis = None
if "next_level" not in st.session_state:
    st.session_state.next_level = None
if "show_continue" not in st.session_state:
    st.session_state.show_continue = False
if "quiz_submitted" not in st.session_state:
    st.session_state.quiz_submitted = False
if "quiz_score" not in st.session_state:
    st.session_state.quiz_score = None
if "quiz_answers" not in st.session_state:
    st.session_state.quiz_answers = None

# ---- Timer State Init ----
if "timer_started" not in st.session_state:
    st.session_state.timer_started = False
if "start_time" not in st.session_state:
    st.session_state.start_time = None
if "is_break" not in st.session_state:
    st.session_state.is_break = False
if "elapsed_before_pause" not in st.session_state:
    st.session_state.elapsed_before_pause = 0
if "white_noise_playing" not in st.session_state:
    st.session_state.white_noise_playing = False

# Page configuration
st.set_page_config(
    page_title="FocusMate LMS", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize cookie manager
cookie_manager = get_cookie_manager()

# Check for existing login
if 'current_user' not in st.session_state:
    # Try to get user data from cookie
    user_cookie = cookie_manager.get('user_data')
    if user_cookie:
        try:
            user_data = json.loads(user_cookie)
            st.session_state.user_id = user_data['id']
            st.session_state.current_user = (
                user_data['id'],
                user_data['name'],
                user_data['email'],
                user_data['experience_level'],
                user_data['learning_goals']
            )
            st.session_state.authentication_status = True
        except:
            st.session_state.current_user = None
            st.session_state.user_id = None
            st.session_state.authentication_status = None

# Database setup
def init_database():
    conn = sqlite3.connect('focusmate.db')
    cursor = conn.cursor()
    
    # Users table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE,
        experience_level TEXT,
        learning_goals TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Courses table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS courses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        category TEXT,
        total_chapters INTEGER,
        total_lectures INTEGER,
        difficulty_level TEXT,
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # User progress table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        course_id INTEGER,
        progress_percentage REAL,
        overall_score REAL,
        status TEXT,
        last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id),
        FOREIGN KEY (course_id) REFERENCES courses (id)
    )
    ''')
    
    # Challenges table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS challenges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id INTEGER,
        level INTEGER,
        title TEXT NOT NULL,
        description TEXT,
        video_url TEXT,
        quiz_data TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (course_id) REFERENCES courses (id)
    )
    ''')
    
    # User reflections table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS reflections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        challenge_id INTEGER,
        reflection_text TEXT,
        ai_feedback TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id),
        FOREIGN KEY (challenge_id) REFERENCES challenges (id)
    )
    ''')
    
    # Quiz attempts table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS quiz_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        challenge_id INTEGER,
        answers TEXT,
        score REAL,
        completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id),
        FOREIGN KEY (challenge_id) REFERENCES challenges (id)
    )
    ''')
    
    # Study sessions table (for Pomodoro tracking)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS study_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        session_type TEXT,
        duration_minutes INTEGER,
        completed BOOLEAN,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )
    ''')
    
    conn.commit()
    conn.close()

# Initialize database
init_database()
def add_missing_column():
    conn = sqlite3.connect('focusmate.db')
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE user_progress ADD COLUMN last_challenge_id INTEGER")
    except sqlite3.OperationalError:
        pass  # column exists
    conn.commit()
    conn.close()

add_missing_column()
# Session state initialization
def init_session_state():
    # First try to restore the session from st.session_state
    if 'current_user' in st.session_state and st.session_state.current_user:
        return

    defaults = {
        'user_id': None,
        'current_user': None,
        'timer_started': False,
        'start_time': None,
        'is_break': False,
        'elapsed_before_pause': 0,
        'current_course': None,
        'selected_challenge': None,
        'show_reflection': False,
        'current_feedback': None,
        'current_exercise': None,
        'submitted_code': None,
        'selected_course_id': None,
        'selected_course_name': None,
        'current_level': 1,
        'authentication_status': None
    }
    
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

# After the session state initialization section, add code state persistence
if "code_states" not in st.session_state:
    st.session_state.code_states = {}

init_session_state()

def create_user(name, email, experience_level, learning_goals):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
        INSERT INTO users (name, email, experience_level, learning_goals)
        VALUES (?, ?, ?, ?)
        ''', (name, email, experience_level, learning_goals))
        user_id = cursor.lastrowid
        conn.commit()
        return user_id
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()

def get_user_by_email(email):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE email = ?', (email,))
    user = cursor.fetchone()
    conn.close()
    return user

def populate_sample_data_v2():
    if st.session_state.get("sample_data_loaded"):
        return
    conn = get_db_connection()
    cursor = conn.cursor()

    # Check if courses already exist
    cursor.execute('SELECT COUNT(*) FROM courses')
    if cursor.fetchone()[0] > 0:
        conn.close()
        return

    # Load courses.json
    json_path = os.path.join(os.path.dirname(__file__), "courses.json")
    if not os.path.exists(json_path):
        st.error("courses.json file not found.")
        return

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    courses = data.get("courses", [])

    course_id = 1  # for assigning FK to challenges manually

    for course in courses:
        course_tuple = (
            course["name"],
            course["category"],
            course["total_chapters"],
            course["total_lectures"],
            course["difficulty_level"],
            course["description"]
        )

        cursor.execute('''
        INSERT INTO courses (name, category, total_chapters, total_lectures, difficulty_level, description)
        VALUES (?, ?, ?, ?, ?, ?)
        ''', course_tuple)

        level = 1
        for video in course.get("videos", []):
            # Create complete video data including all components
            video_data = {
                "intro_text": video.get("intro_text", ""),
                "code_snippets": video.get("code_snippets", []),
                "questions": video.get("quizzes", []),  # Changed from "quizzes" to "questions"
                "coding_exercises": video.get("coding_exercises", []),
                "conclusion_text": video.get("conclusion_text", "")
            }

            challenge = {
                "course_id": course_id,
                "level": level,
                "title": video["title"],
                "description": video.get("description", video["title"]),
                "video_url": video["url"],
                "quiz_data": json.dumps(video_data)  # Store all video data in quiz_data
            }

            cursor.execute('''
            INSERT INTO challenges (course_id, level, title, description, video_url, quiz_data)
            VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                challenge["course_id"],
                challenge["level"],
                challenge["title"],
                challenge["description"],
                challenge["video_url"],
                challenge["quiz_data"]
            ))

            level += 1

        course_id += 1

    conn.commit()
    conn.close()
    st.session_state.sample_data_loaded = True

# Initialize database and load sample data only once
init_database()
if "sample_data_loaded" not in st.session_state:
    populate_sample_data_v2()
    st.session_state.sample_data_loaded = True

# Timer functionality
def show_pomodoro_timer():
    st_autorefresh(interval=1000, limit=None, key="timer-refresh")

    # Timer settings
    st.subheader("⚙️ Timer Settings")
    work_minutes = st.slider("Work Duration (minutes)", 15, 60, 25, key="work_duration_slider_main")
    break_minutes = st.slider("Break Duration (minutes)", 5, 20, 5, key="break_duration_slider_main")

    # Convert to seconds
    WORK_DURATION = work_minutes * 60
    BREAK_DURATION = break_minutes * 60

    # Initialize timer states if not present
    if 'timer_started' not in st.session_state:
        st.session_state.timer_started = False
    if 'start_time' not in st.session_state:
        st.session_state.start_time = None
    if 'elapsed_before_pause' not in st.session_state:
        st.session_state.elapsed_before_pause = 0
    if 'current_session_id' not in st.session_state:
        st.session_state.current_session_id = None
    if 'is_paused' not in st.session_state:
        st.session_state.is_paused = False
    if 'is_break' not in st.session_state:
        st.session_state.is_break = False
    if 'work_elapsed' not in st.session_state:
        st.session_state.work_elapsed = 0
    if 'break_elapsed' not in st.session_state:
        st.session_state.break_elapsed = 0

    st.markdown("### ⏳ Pomodoro Timer")

    col1, col2, col3 = st.columns(3)
    
    with col1:
        # Dynamic button text based on timer state
        if st.session_state.timer_started:
            button_text = "⏸️ Pause"
        elif st.session_state.is_paused:
            button_text = "▶️ Resume"
        else:
            button_text = "▶️ Start Work"

        if st.button(button_text, use_container_width=True):
            if not st.session_state.timer_started and not st.session_state.is_paused:
                # Starting new timer
                st.session_state.start_time = time.time()
                st.session_state.timer_started = True
                st.session_state.is_paused = False
                
                # Create new session if not resuming
                if not st.session_state.current_session_id:
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute('''
                    INSERT INTO study_sessions (user_id, session_type, duration_minutes, completed)
                    VALUES (?, ?, ?, ?)
                    ''', (st.session_state.user_id, 'Work' if not st.session_state.is_break else 'Break', 0, False))
                    st.session_state.current_session_id = cursor.lastrowid
                    conn.commit()
                    conn.close()
            
            elif st.session_state.timer_started:
                # Pausing timer
                st.session_state.timer_started = False
                st.session_state.is_paused = True
                if st.session_state.start_time:
                    current_elapsed = int(time.time() - st.session_state.start_time)
                    if st.session_state.is_break:
                        st.session_state.break_elapsed += current_elapsed
                    else:
                        st.session_state.work_elapsed += current_elapsed
                st.session_state.start_time = None
            
            else:
                # Resuming timer
                st.session_state.start_time = time.time()
                st.session_state.timer_started = True
                st.session_state.is_paused = False
            st.rerun()

    with col2:
        if st.button("⏹️ Reset", use_container_width=True):
            # Complete the current session if exists
            if st.session_state.current_session_id:
                conn = get_db_connection()
                cursor = conn.cursor()
                total_elapsed = st.session_state.work_elapsed if not st.session_state.is_break else st.session_state.break_elapsed
                cursor.execute('''
                UPDATE study_sessions 
                SET duration_minutes = ?, completed = 1
                WHERE id = ?
                ''', (total_elapsed // 60, st.session_state.current_session_id))
                conn.commit()
                conn.close()
            
            # Reset all timer states
            st.session_state.timer_started = False
            st.session_state.start_time = None
            st.session_state.elapsed_before_pause = 0
            st.session_state.current_session_id = None
            st.session_state.is_paused = False
            st.session_state.is_break = False
            st.session_state.work_elapsed = 0
            st.session_state.break_elapsed = 0
            st.rerun()

    with col3:
        # Switch between work/break
        if st.button("🔄 Switch to " + ("Work" if st.session_state.is_break else "Break"), use_container_width=True):
            # Store current progress before switching
            if st.session_state.timer_started and st.session_state.start_time:
                current_elapsed = int(time.time() - st.session_state.start_time)
                if st.session_state.is_break:
                    st.session_state.break_elapsed += current_elapsed
                else:
                    st.session_state.work_elapsed += current_elapsed

            # Complete current session if exists
            if st.session_state.current_session_id:
                conn = get_db_connection()
                cursor = conn.cursor()
                total_elapsed = st.session_state.work_elapsed if not st.session_state.is_break else st.session_state.break_elapsed
                cursor.execute('''
                UPDATE study_sessions 
                SET duration_minutes = ?, completed = 1
                WHERE id = ?
                ''', (total_elapsed // 60, st.session_state.current_session_id))
                conn.commit()
                conn.close()

            # Switch mode and start new session
            st.session_state.is_break = not st.session_state.is_break
            st.session_state.timer_started = True
            st.session_state.start_time = time.time()
            
            # Create new session
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
            INSERT INTO study_sessions (user_id, session_type, duration_minutes, completed)
            VALUES (?, ?, ?, ?)
            ''', (st.session_state.user_id, 'Break' if st.session_state.is_break else 'Work', 0, False))
            st.session_state.current_session_id = cursor.lastrowid
            conn.commit()
            conn.close()
            st.rerun()

    timer_placeholder = st.empty()

    if st.session_state.timer_started and st.session_state.start_time:
        duration = BREAK_DURATION if st.session_state.is_break else WORK_DURATION
        current_elapsed = int(time.time() - st.session_state.start_time)
        total_elapsed = current_elapsed + (st.session_state.break_elapsed if st.session_state.is_break else st.session_state.work_elapsed)
        remaining = duration - total_elapsed

        if remaining <= 0:
            # Timer completed
            if st.session_state.is_break:
                st.session_state.break_elapsed = 0
            else:
                st.session_state.work_elapsed = 0
                
            st.session_state.is_break = not st.session_state.is_break
            st.session_state.timer_started = True
            st.session_state.start_time = time.time()
            
            # Complete the current session
            if st.session_state.current_session_id:
                conn = get_db_connection()
                cursor = conn.cursor()
                total_elapsed = duration // 60  # Use full duration since timer completed
                cursor.execute('''
                UPDATE study_sessions 
                SET duration_minutes = ?, completed = 1
                WHERE id = ?
                ''', (total_elapsed, st.session_state.current_session_id))
                conn.commit()
                conn.close()

            # Create new session
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
            INSERT INTO study_sessions (user_id, session_type, duration_minutes, completed)
            VALUES (?, ?, ?, ?)
            ''', (st.session_state.user_id, 'Break' if st.session_state.is_break else 'Work', 0, False))
            st.session_state.current_session_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            st.success(f"✅ {'Work' if not st.session_state.is_break else 'Break'} session complete! Starting {'Break' if st.session_state.is_break else 'Work'} timer...")
            st.rerun()
        else:
            mins, secs = divmod(remaining, 60)
            label = "Break" if st.session_state.is_break else "Work"
            timer_placeholder.info(f"⏱️ {label} Time Remaining: {mins:02d}:{secs:02d}")
            
            # Show total elapsed time for current mode
            total_mode_elapsed = total_elapsed
            total_mins, total_secs = divmod(total_mode_elapsed, 60)
            st.caption(f"Total {label} time: {total_mins:02d}:{total_secs:02d}")
    elif st.session_state.is_paused:
        duration = BREAK_DURATION if st.session_state.is_break else WORK_DURATION
        total_elapsed = st.session_state.break_elapsed if st.session_state.is_break else st.session_state.work_elapsed
        remaining = duration - total_elapsed
        mins, secs = divmod(remaining, 60)
        label = "Break" if st.session_state.is_break else "Work"
        timer_placeholder.info(f"⏱️ {label} (Paused) Time Remaining: {mins:02d}:{secs:02d}")
        
        # Show total elapsed time for current mode
        total_mins, total_secs = divmod(total_elapsed, 60)
        st.caption(f"Total {label} time: {total_mins:02d}:{total_secs:02d}")
    else:
        timer_placeholder.info("⏱️ Timer not running. Click ▶️ to begin.")

# Achievement streak calculation function
def calculate_achievement_streak(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get all activity dates (study sessions and reflections) ordered by date
    cursor.execute('''
    WITH activity_dates AS (
        SELECT DATE(created_at) as activity_date
        FROM study_sessions
        WHERE user_id = ? AND completed = 1
        UNION
        SELECT DATE(created_at) as activity_date
        FROM reflections
        WHERE user_id = ?
    )
    SELECT DISTINCT activity_date
    FROM activity_dates
    ORDER BY activity_date DESC
    ''', (user_id, user_id))
    
    dates = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    if not dates:
        return 0
        
    # Calculate streak
    streak = 1
    today = datetime.datetime.now().date()
    last_date = datetime.datetime.strptime(dates[0], '%Y-%m-%d').date()
    
    # If no activity today, check if there was activity yesterday to continue streak
    if (today - last_date).days > 1:
        return 0
    
    # Calculate consecutive days
    for i in range(len(dates) - 1):
        current_date = datetime.datetime.strptime(dates[i], '%Y-%m-%d').date()
        next_date = datetime.datetime.strptime(dates[i + 1], '%Y-%m-%d').date()
        
        if (current_date - next_date).days == 1:
            streak += 1
        else:
            break
    
    return streak

# Custom CSS for better styling
def load_css():
    st.markdown("""
    <style>
    .metric-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1rem;
        border-radius: 10px;
        color: white;
        text-align: center;
        margin: 0.5rem 0;
    }
    
    .course-card {
        background: white;
        padding: 1.5rem;
        border-radius: 10px;
        box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        margin: 1rem 0;
        border-left: 4px solid #667eea;
    }
    
    .progress-ring {
        display: inline-block;
        position: relative;
        width: 120px;
        height: 120px;
        margin: 1rem;
    }
    
    .challenge-card {
        background: linear-gradient(135deg, #ff9a9e 0%, #fecfef 50%, #fecfef 100%);
        padding: 1.5rem;
        border-radius: 15px;
        margin: 1rem 0;
        box-shadow: 0 4px 15px rgba(0,0,0,0.1);
    }
    
    .sidebar .sidebar-content {
        background: linear-gradient(180deg, #667eea 0%, #764ba2 100%);
    }
    
    .stProgress .st-bo {
        background-color: #667eea;
    }

    /* Custom styling for primary buttons */
    .stButton > button {
        background-color: #667eea !important;
        color: white !important;
        border: none !important;
        border-radius: 5px !important;
        transition: all 0.3s ease !important;
    }
    
    .stButton > button:hover {
        background-color: #764ba2 !important;
        box-shadow: 0 2px 10px rgba(0,0,0,0.1) !important;
    }
    
    .stButton > button:active {
        transform: scale(0.98) !important;
    }
    </style>
    """, unsafe_allow_html=True)

load_css()

# Main navigation
with st.sidebar:
    # User info section
    if st.session_state.current_user:
        st.markdown(f"### 👋 Welcome, {st.session_state.current_user[1]}!")
        st.markdown(f"**Level:** {st.session_state.current_user[3]}")
    
    menu_options = ["Dashboard", "Profile", "My Courses", "Learning Path", "Challenges", "Progress Analytics", "Study Timer"]
    
    # Get the current page from query params or session state
    if 'selected' not in st.session_state:
        st.session_state.selected = "Dashboard"
    
    selected = option_menu(
        "FocusMate",
        menu_options,
        icons=["house", "person", "book", "map", "puzzle", "graph-up", "clock"],
        menu_icon="graduation-cap",
        default_index=menu_options.index(st.session_state.selected),
        styles={
            "container": {"padding": "0!important", "background-color": "#fafafa"},
            "icon": {"color": "#667eea", "font-size": "18px"},
            "nav-link": {"font-size": "16px", "text-align": "left", "margin": "0px", "--hover-color": "#eee"},
            "nav-link-selected": {"background-color": "#667eea"},
        },
        key='nav_menu'
    )
    
    # If menu selection changes, update the session state and rerun
    if selected != st.session_state.selected:
        st.session_state.selected = selected
        st.rerun()

# Dashboard Page
if selected == "Dashboard":
    if not st.session_state.current_user:
        st.warning("Please set up your profile first!")
        st.stop()
    
    st.title("📊 Learning Dashboard")
    
    # Get user statistics
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get enrolled courses
    cursor.execute('''
    SELECT COUNT(*) FROM user_progress WHERE user_id = ?
    ''', (st.session_state.user_id,))
    total_courses = cursor.fetchone()[0]
    
    # Get completed courses
    cursor.execute('''
    SELECT COUNT(*) FROM user_progress 
    WHERE user_id = ? AND status = 'Completed'
    ''', (st.session_state.user_id,))
    completed_courses = cursor.fetchone()[0]
    
    # Get average progress
    cursor.execute('''
    SELECT AVG(progress_percentage) FROM user_progress WHERE user_id = ?
    ''', (st.session_state.user_id,))
    avg_progress = cursor.fetchone()[0] or 0
    
    # Get study sessions this week
    cursor.execute('''
    SELECT COUNT(*) FROM study_sessions 
    WHERE user_id = ? AND created_at >= date('now', '-7 days')
    ''', (st.session_state.user_id,))
    weekly_sessions = cursor.fetchone()[0]
    
    conn.close()
    
    # Performance metrics
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.markdown(f"""
        <div class="metric-card">
            <h3>{total_courses}</h3>
            <p>Total Enrolled</p>
        </div>
        """, unsafe_allow_html=True)
    
    with col2:
        st.markdown(f"""
        <div class="metric-card">
            <h3>{completed_courses}</h3>
            <p>Completed</p>
        </div>
        """, unsafe_allow_html=True)
    
    with col3:
        st.markdown(f"""
        <div class="metric-card">
            <h3>{int(avg_progress)}%</h3>
            <p>Avg Progress</p>
        </div>
        """, unsafe_allow_html=True)
    
    with col4:
        st.markdown(f"""
        <div class="metric-card">
            <h3>{weekly_sessions}</h3>
            <p>Study Sessions</p>
        </div>
        """, unsafe_allow_html=True)
    
   
    
    # Recent activity and upcoming items
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("📚 My Courses")
        
        # Get user's courses with more detailed info
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
        SELECT c.name, up.progress_percentage, up.overall_score, up.status, c.difficulty_level,
               (SELECT COUNT(*) FROM reflections r 
                JOIN challenges ch ON r.challenge_id = ch.id 
                WHERE ch.course_id = c.id AND r.user_id = up.user_id) as reflection_count
        FROM courses c
        JOIN user_progress up ON c.id = up.course_id
        WHERE up.user_id = ?
        ORDER BY up.last_accessed DESC
        ''', (st.session_state.user_id,))
        user_courses = cursor.fetchall()
        conn.close()
        
        if user_courses:
            for course in user_courses:
                progress_color = "success" if course[3] == "Completed" else "info"
                st.markdown(f"""
                <div class="course-card">
                    <h4>{course[0]}</h4>
                    <p><strong>Level:</strong> {course[4]}</p>
                    <p><strong>Progress:</strong> {int(course[1] or 0)}%</p>
                    <p><strong>Score:</strong> {int(course[2] or 0)}%</p>
                    <p><strong>Reflections:</strong> {course[5]}</p>
                    <span class="badge badge-{progress_color}">{course[3]}</span>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("No courses enrolled yet. Check out the Learning Path!")
    
    with col2:
        st.markdown("""
        <div style='background: linear-gradient(135deg, #FF9D6C 0%, #FF6B6B 100%); padding: 1.5rem; border-radius: 15px; margin-bottom: 1rem;'>
            <h3 style='color: white; margin: 0; text-align: center;'>🏆 Achievement Streak</h3>
        </div>
        """, unsafe_allow_html=True)
        
        # Calculate current streak
        streak = calculate_achievement_streak(st.session_state.user_id)
        
        # Display streak in a fancy way
        st.markdown(f"""
        <div style='text-align: center; padding: 1rem; background: white; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); margin-bottom: 1rem;'>
            <h1 style='color: #FF6B6B; font-size: 3rem; margin: 0;'>{streak}</h1>
            <p style='color: #666; margin: 0;'>Days in a Row</p>
        </div>
        """, unsafe_allow_html=True)
        
        # Visual representation with improved design
        st.markdown("""
        <div style='background: white; padding: 1rem; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); margin-bottom: 1rem;'>
            <p style='color: #666; margin-bottom: 0.5rem; text-align: center;'>Your Streak Journey</p>
        """, unsafe_allow_html=True)
        
        cols = st.columns(7)
        for i in range(7):
            if i < streak:
                cols[i].markdown(
                    f"""<div style='text-align: center;'>
                        <div style='font-size: 1.5rem; color: #FF6B6B;'>🔥</div>
                        <div style='font-size: 0.8rem; color: #666;'>Day {i+1}</div>
                    </div>""", 
                    unsafe_allow_html=True
                )
            else:
                cols[i].markdown(
                    f"""<div style='text-align: center;'>
                        <div style='font-size: 1.5rem; color: #DDD;'>⚪</div>
                        <div style='font-size: 0.8rem; color: #999;'>Day {i+1}</div>
                    </div>""",
                    unsafe_allow_html=True
                )
        
        st.markdown("</div>", unsafe_allow_html=True)
        
        # Show achievements with better styling
        st.markdown("""
        <div style='background: white; padding: 1rem; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);'>
            <p style='color: #666; margin-bottom: 0.5rem; text-align: center;'>Achievements Unlocked</p>
        """, unsafe_allow_html=True)
        
        if streak >= 3:
            st.markdown("""
            <div style='background: linear-gradient(135deg, #FFF3E6 0%, #FFE5CC 100%); padding: 0.5rem; border-radius: 5px; margin-bottom: 0.5rem;'>
                <p style='color: #FF9D6C; margin: 0;'>🎯 3-Day Warrior</p>
            </div>
            """, unsafe_allow_html=True)
        if streak >= 5:
            st.markdown("""
            <div style='background: linear-gradient(135deg, #FFE5E5 0%, #FFD6D6 100%); padding: 0.5rem; border-radius: 5px; margin-bottom: 0.5rem;'>
                <p style='color: #FF6B6B; margin: 0;'>🔥 5-Day Champion</p>
            </div>
            """, unsafe_allow_html=True)
        if streak >= 7:
            st.markdown("""
            <div style='background: linear-gradient(135deg, #E6F9FF 0%, #CCF2FF 100%); padding: 0.5rem; border-radius: 5px; margin-bottom: 0.5rem;'>
                <p style='color: #3CAEA3; margin: 0;'>⭐ 7-Day Master</p>
            </div>
            """, unsafe_allow_html=True)
        
        st.markdown("</div>", unsafe_allow_html=True)
        
        # Motivational message based on streak
        if streak == 0:
            st.markdown("""
            <div style='text-align: center; padding: 1rem; color: #666; font-style: italic;'>
                Start your streak today! 💪
            </div>
            """, unsafe_allow_html=True)
        elif streak < 3:
            st.markdown("""
            <div style='text-align: center; padding: 1rem; color: #666; font-style: italic;'>
                Great start! Keep going to unlock more achievements! 🌟
            </div>
            """, unsafe_allow_html=True)
        elif streak < 5:
            st.markdown("""
            <div style='text-align: center; padding: 1rem; color: #666; font-style: italic;'>
                You're on fire! The 5-Day Champion badge awaits! 🔥
            </div>
            """, unsafe_allow_html=True)
        elif streak < 7:
            st.markdown("""
            <div style='text-align: center; padding: 1rem; color: #666; font-style: italic;'>
                Almost there! The 7-Day Master badge is within reach! ⭐
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div style='text-align: center; padding: 1rem; color: #666; font-style: italic;'>
                Incredible dedication! You're a true learning master! 👑
            </div>
            """, unsafe_allow_html=True)

# Profile Setup Page
elif selected == "Profile":
    if st.session_state.white_noise_playing:
        show_white_noise_player("profile", show_controls=False)

    # First check if user is not logged in
    if not st.session_state.current_user:
        st.title("Welcome to FocusMate! 👋")
        
        # Login section first
        st.markdown("### 🔑 Login to Your Account")
        login_email = st.text_input("Your Email", key="login_email")
        if st.button("Login", use_container_width=True):
            user = get_user_by_email(login_email)
            if user:
                st.session_state.user_id = user[0]
                st.session_state.current_user = user
                st.session_state.authentication_status = True
                # Store user data in cookie
                user_data = {
                    'id': user[0],
                    'name': user[1],
                    'email': user[2],
                    'experience_level': user[3],
                    'learning_goals': user[4]
                }
                cookie_manager.set('user_data', json.dumps(user_data), expires_at=datetime.datetime.now() + datetime.timedelta(days=30))
                st.success(f"Welcome back, {user[1]}!")
                st.rerun()
            else:
                st.error("User not found. Please check your email or create a new profile below.")
        
        # Separator
        st.markdown("---")
        
        # New Profile section
        st.title("👤 Create New Profile")
        
        col1, col2 = st.columns([2, 1])

        # Load saved values if user is logged in
        saved_name = ""
        saved_email = ""
        saved_experience = "Beginner"
        saved_goals = []

        with col1:
            with st.form("profile_form"):
                name = st.text_input("Full Name", value=saved_name)
                email = st.text_input("Email Address", value=saved_email)
                experience = st.selectbox("Experience Level", ["Beginner", "Intermediate", "Advanced"])
                goals = st.multiselect("Learning Goals", 
                                   ["Creativity", "Problem Solving", "Science", "Mathematics", "Programming"])

                if st.form_submit_button("Create Profile", use_container_width=True):
                    if name and email:
                        user_id = create_user(name, email, experience, ", ".join(goals))
                        if user_id:
                            st.session_state.user_id = user_id
                            st.session_state.current_user = (user_id, name, email, experience, ", ".join(goals))
                            st.session_state.authentication_status = True
                            # Store user data in cookie
                            user_data = {
                                'id': user_id,
                                'name': name,
                                'email': email,
                                'experience_level': experience,
                                'learning_goals': ", ".join(goals)
                            }
                            cookie_manager.set('user_data', json.dumps(user_data), expires_at=datetime.datetime.now() + datetime.timedelta(days=30))
                            st.success("Profile created successfully!")
                            st.rerun()
                        else:
                            st.error("Email already exists. Please use a different email.")
                    else:
                        st.error("Please fill in all required fields.")

        with col2:
            st.markdown("### 🎯 Why Profile Matters")
            st.info("""
            - **Personalized Learning**: Get recommendations based on your level
            - **Progress Tracking**: Monitor your improvement over time
            - **Achievement System**: Unlock badges and milestones
            - **Smart Scheduling**: Optimize study sessions
            """)
    
    else:
        st.title("👤 Profile Setup")
        
        col1, col2 = st.columns([2, 1])
        
        # Load saved values if user is logged in
        saved_name = st.session_state.current_user[1]
        saved_email = st.session_state.current_user[2]
        saved_experience = st.session_state.current_user[3]
        saved_goals = [g.strip() for g in st.session_state.current_user[4].split(",")]
        
        with col1:
            with st.form("profile_form"):
                name = st.text_input("Full Name", value=saved_name)
                email = st.text_input("Email Address", value=saved_email, disabled=True)  # locked since user exists
                experience = st.selectbox("Experience Level", ["Beginner", "Intermediate", "Advanced"], 
                                       index=["Beginner", "Intermediate", "Advanced"].index(saved_experience))
                goals = st.multiselect("Learning Goals", 
                                   ["Creativity", "Problem Solving", "Science", "Mathematics", "Programming"],
                                   default=saved_goals)

                col_btn1, col_btn2 = st.columns(2)
                with col_btn1:
                    if st.form_submit_button("Save Changes", use_container_width=True):
                        st.info("Profile updates will be available soon!")
                
                with col_btn2:
                    if st.form_submit_button("Logout", type="secondary", use_container_width=True):
                        cookie_manager.delete('user_data')
                        st.session_state.current_user = None
                        st.session_state.user_id = None
                        st.session_state.authentication_status = None
                        st.rerun()

        with col2:
            st.markdown("### 🎯 Quick Stats")
            # Add some quick statistics
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # Get enrolled courses count
            cursor.execute('SELECT COUNT(*) FROM user_progress WHERE user_id = ?', (st.session_state.user_id,))
            enrolled_courses = cursor.fetchone()[0]
            
            # Get completed courses
            cursor.execute('SELECT COUNT(*) FROM user_progress WHERE user_id = ? AND status = "Completed"', 
                         (st.session_state.user_id,))
            completed_courses = cursor.fetchone()[0]
            
            # Get total study minutes (only from completed sessions)
            cursor.execute('''
            SELECT COALESCE(SUM(duration_minutes), 0)
            FROM study_sessions 
            WHERE user_id = ? AND completed = 1
            ''', (st.session_state.user_id,))
            total_study_minutes = cursor.fetchone()[0] or 0
            
            conn.close()
            
            st.metric("Enrolled Courses", enrolled_courses)
            st.metric("Completed Courses", completed_courses)
            
            # Format study time nicely
            if total_study_minutes >= 60:
                hours = total_study_minutes // 60
                minutes = total_study_minutes % 60
                study_time_display = f"{hours}h {minutes}m"
            else:
                study_time_display = f"{total_study_minutes}m"
            st.metric("Total Study Time", study_time_display)

# My Courses Page
elif selected == "My Courses":
    if st.session_state.white_noise_playing:
        show_white_noise_player("courses", show_controls=False)
    st.title("📚 My Courses")
    
    if not st.session_state.current_user:
        st.warning("Please set up your profile first!")
        st.stop()
    
    # Get all available courses
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM courses')
    all_courses = cursor.fetchall()
    
    # Get user's enrolled courses
    cursor.execute('''
    SELECT course_id FROM user_progress WHERE user_id = ?
    ''', (st.session_state.user_id,))
    enrolled_course_ids = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    tabs = st.tabs(["Available Courses", "My Enrolled Courses"])
    
    with tabs[0]:
        st.subheader("🌟 Available Courses")
        
        # Group courses by category
        categories = {}
        for course in all_courses:
            category = course[2]
            if category not in categories:
                categories[category] = []
            categories[category].append(course)
        
        for category, courses in categories.items():
            st.markdown(f"### {category}")
            
            for course in courses:
                course_id, name, _, total_chapters, total_lectures, difficulty, description = course[:7]
                
                with st.expander(f"{name} - {difficulty}"):
                    col1, col2 = st.columns([3, 1])
                    
                    with col1:
                        st.write(f"**Description:** {description}")
                        st.write(f"**Chapters:** {total_chapters} | **Lectures:** {total_lectures}")
                        st.write(f"**Difficulty:** {difficulty}")
                    
                    with col2:
                        if course_id in enrolled_course_ids:
                            st.success("✅ Enrolled")
                        else:
                            if st.button(f"Enroll", key=f"enroll_{course_id}"):
                                # Enroll user in course
                                conn = get_db_connection()
                                cursor = conn.cursor()
                                cursor.execute('''
                                INSERT INTO user_progress (user_id, course_id, progress_percentage, overall_score, status)
                                VALUES (?, ?, ?, ?, ?)
                                ''', (st.session_state.user_id, course_id, 0, 0, "In Progress"))
                                conn.commit()
                                conn.close()
                                st.success("Enrolled successfully!")
                                st.rerun()
    
    with tabs[1]:
        st.subheader("📖 My Enrolled Courses")

        # Get detailed info about enrolled courses
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
        SELECT c.*, up.progress_percentage, up.overall_score, up.status, up.last_accessed
        FROM courses c
        JOIN user_progress up ON c.id = up.course_id
        WHERE up.user_id = ?
        ORDER BY up.last_accessed DESC
        ''', (st.session_state.user_id,))
        enrolled_courses = cursor.fetchall()
        conn.close()
        
        if enrolled_courses:
            for course in enrolled_courses:
                print(course)
                course_id, name, category, total_chapters, total_lectures, difficulty, description = course[:7]
                progress, score, status, last_accessed = course[8:12]
                
                with st.container():
                    st.markdown(f"""
                    <div class="course-card">
                        <h3>{name}</h3>
                        <p>{description}</p>
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <div>
                                <strong>Progress:</strong> {str(progress or 0)}% | 
                                <strong>Score:</strong> {int(score or 0)}% | 
                                <strong>Status:</strong> {status}
                            </div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    col1, col2, col3 = st.columns([1, 1, 1])
                    # with col1:
                    #     if st.button(f"Continue Learning", key=f"continue_{course_id}"):
                    #         st.session_state.current_course = course_id
                    #         st.session_state.selected = "Learning Path"
                    #         st.rerun()
                    
                    with col2:
                        st.progress(int(progress or 0) / 100)
                    
                    with col3:
                        difficulty_color = {"Beginner": "🟢", "Intermediate": "🟡", "Advanced": "🔴"}
                        st.markdown(f"{difficulty_color.get(difficulty, '⚪')} {difficulty}")
        else:
            st.info("No courses enrolled yet. Browse available courses above!")

# Learning Path Page
elif selected == "Learning Path":
    if st.session_state.white_noise_playing:
        show_white_noise_player("learning_path", show_controls=False)
    st.title("🗺️ Learning Path")
    
    if not st.session_state.current_user:
        st.warning("Please set up your profile first!")
        st.stop()
    
    # Course selection
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    SELECT c.id, c.name FROM courses c
    JOIN user_progress up ON c.id = up.course_id
    WHERE up.user_id = ?
    ''', (st.session_state.user_id,))
    user_courses = cursor.fetchall()
    conn.close()
    
    if not user_courses:
        st.warning("Please enroll in a course first from 'My Courses' section!")
        st.stop()
    
    # Course selector
    course_options = {course[1]: course[0] for course in user_courses}
    selected_course_name = st.selectbox("Select Course", list(course_options.keys()))
    selected_course_id = course_options[selected_course_name]

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    SELECT DISTINCT ch.id, ch.course_id, ch.level, ch.title, ch.description, ch.video_url, ch.quiz_data
    FROM challenges ch
    WHERE ch.course_id = ?
    ORDER BY ch.level
    ''', (selected_course_id,))
    challenges = cursor.fetchall()
    conn.close()

    # Display challenges
    for i, challenge in enumerate(challenges):
        challenge_id, course_id, level, title, description, video_url, quiz_data = challenge[:7]
        
        with st.expander(f"Level {level}: {title}", expanded=i == 0):
            # Get the video data
            video_data = json.loads(quiz_data)
            
            # Show intro text if it exists
            if "intro_text" in video_data and video_data["intro_text"]:
                st.markdown("### 📝 Introduction")
                st.write(video_data["intro_text"])
            
            # Display video if URL exists
            if video_url:
                st.markdown("### 📺 Video Lesson")
                # Extract video ID from URL
                video_id = video_url.split("v=")[-1] if "v=" in video_url else video_url.split("/")[-1]
                st.video(f"https://youtube.com/watch?v={video_id}")
            
            # Show conclusion text if it exists
            if "conclusion_text" in video_data and video_data["conclusion_text"]:
                st.markdown("### 🎯 Summary")
                st.write(video_data["conclusion_text"])

# Challenges Page
elif selected == "Challenges":
    if st.session_state.white_noise_playing:
        show_white_noise_player("challenges", show_controls=False)
    st.title("🧩 Interactive Challenges")
    
    if not st.session_state.current_user:
        st.warning("Please set up your profile first!")
        st.stop()
    
    # Get user's courses
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    SELECT DISTINCT c.id, c.name
    FROM courses c
    JOIN user_progress up ON c.id = up.course_id
    WHERE up.user_id = ?
    ORDER BY c.name
    ''', (st.session_state.user_id,))
    available_courses = cursor.fetchall()
    conn.close()
    
    if not available_courses:
        st.warning("No courses available. Please enroll in courses first!")
        st.stop()
    
    # Course selector
    course_options = {course[1]: course[0] for course in available_courses}
    
    # Use session state to maintain course selection
    if st.session_state.selected_course_name not in course_options:
        st.session_state.selected_course_name = list(course_options.keys())[0]
        st.session_state.selected_course_id = course_options[st.session_state.selected_course_name]
    
    selected_course_name = st.selectbox(
        "Select Course",
        list(course_options.keys()),
        index=list(course_options.keys()).index(st.session_state.selected_course_name)
    )
    
    # Update session state if course selection changes
    if selected_course_name != st.session_state.selected_course_name:
        st.session_state.selected_course_name = selected_course_name
        st.session_state.selected_course_id = course_options[selected_course_name]
        st.session_state.show_reflection = False
        st.session_state.current_feedback = None
        st.session_state.submitted_code = None
        st.session_state.current_exercise = None
        st.rerun()
    
    # Initialize current level if not set
    if st.session_state.current_level == 1:
        current_challenge = get_challenge_by_level(st.session_state.selected_course_id, 1)
    else:
        current_challenge = get_challenge_by_level(st.session_state.selected_course_id, st.session_state.current_level)
    
    if current_challenge:
        challenge_id, course_id, level, title, description, video_url, quiz_data = current_challenge
        
        st.markdown(f"## Level {level}: {title}")
        st.markdown(f"**Course:** {selected_course_name}")
        
        # Get the video data
        video_data = json.loads(quiz_data)
        
        # Show intro text if it exists
        if "intro_text" in video_data and video_data["intro_text"]:
            st.markdown("### 📝 Introduction")
            st.write(video_data["intro_text"])
        
        # Quiz section
        has_quiz = "questions" in video_data and isinstance(video_data["questions"], list) and len(video_data["questions"]) > 0
        has_coding = "coding_exercises" in video_data and isinstance(video_data["coding_exercises"], list) and len(video_data["coding_exercises"]) > 0
        
        if has_quiz:
            st.markdown("### 📋 Knowledge Check")
            
            # Initialize quiz state for this specific challenge
            quiz_state_key = f"quiz_state_{challenge_id}"
            if quiz_state_key not in st.session_state:
                st.session_state[quiz_state_key] = {
                    "submitted": False,
                    "score": None,
                    "answers": None
                }
            
            # Show quiz results if submitted
            if st.session_state[quiz_state_key]["submitted"] and st.session_state[quiz_state_key]["answers"]:
                for i, q in enumerate(video_data["questions"]):
                    st.write(f"**Q{i+1}. {q['question']}**")
                    answer_data = st.session_state[quiz_state_key]["answers"][i]
                    selected_index = q["options"].index(answer_data["selected"]) if answer_data["selected"] in q["options"] else 0
                    st.radio(
                        "Your answer:",
                        q["options"],
                        key=f"quiz_result_{challenge_id}_{i}",
                        index=selected_index,
                        disabled=True
                    )
                    if answer_data["selected"] == answer_data["correct"]:
                        st.success(f"✅ Correct! Your answer: {answer_data['selected']}")
                    else:
                        st.error(f"❌ Incorrect. Your answer: {answer_data['selected']}")
                        st.info(f"The correct answer was: {answer_data['correct']}")
                    
                    st.success(f"Overall Quiz Score: {st.session_state[quiz_state_key]['score']:.1f}%")
            
            # Show quiz form if not submitted
            else:
                with st.form(f"quiz_form_{challenge_id}"):
                    user_answers = []
                    for i, q in enumerate(video_data["questions"]):
                        st.write(f"**Q{i+1}. {q['question']}**")
                        answer = st.radio(
                            "Select your answer:",
                            q["options"],
                            key=f"quiz_{challenge_id}_{i}"
                        )
                        user_answers.append({
                            "question": q["question"],
                            "selected": answer,
                            "correct": q["correct"]
                        })
                    
                    if st.form_submit_button("Submit Quiz"):
                        correct = sum(1 for ans in user_answers if ans["selected"] == ans["correct"])
                        score = (correct / len(user_answers)) * 100
                        
                        # Store quiz results in challenge-specific state
                        st.session_state[quiz_state_key]["submitted"] = True
                        st.session_state[quiz_state_key]["score"] = score
                        st.session_state[quiz_state_key]["answers"] = user_answers
                        st.session_state.show_reflection = True
                        
                        # Save quiz attempt to database
                        conn = get_db_connection()
                        cursor = conn.cursor()
                        cursor.execute('''
                        INSERT INTO quiz_attempts (user_id, challenge_id, answers, score)
                        VALUES (?, ?, ?, ?)
                        ''', (st.session_state.user_id, challenge_id, 
                             json.dumps(user_answers), score))
                        conn.commit()
                        conn.close()
                        
                        # Show success message instead of rerunning
                        st.success(f"Quiz submitted successfully! Score: {score:.1f}%")
        
        # Coding exercises section
        if has_coding:
            st.markdown("### 🔥 Coding Challenges")
            for exercise in video_data["coding_exercises"]:
                with st.container():
                    st.markdown(f"#### 🚀 {exercise['title']}")
                    st.markdown(f"**Task:** {exercise['description']}")
                    
                    # Show hints in a container
                    if exercise.get("hints"):
                        with st.expander("💡 Available Hints"):
                            for i, hint in enumerate(exercise["hints"], 1):
                                st.markdown(f"**Hint #{i}:** {hint}")
                    
                    # Code editor container
                    with st.container():
                        exercise_code_key = f"code_{challenge_id}_{exercise['title']}"
                        if exercise_code_key not in st.session_state:
                            st.session_state[exercise_code_key] = exercise.get("starter_code", "# Write your code here")
                        
                        user_code = st_ace(
                            value=st.session_state[exercise_code_key],
                            language="python",
                            theme="monokai",
                            key=f"ace_{challenge_id}_{exercise['title']}",
                            height=300,
                            show_gutter=True,
                            wrap=True,
                            auto_update=True
                        )
                        
                        # Store code changes
                        st.session_state[exercise_code_key] = user_code
                    
                    # Submit and feedback container
                    with st.container():
                        # Submit button
                        submit_button_disabled = not st.session_state[quiz_state_key]["submitted"] if quiz_state_key in st.session_state else True
                        if st.button("Submit Code", key=f"submit_{challenge_id}_{exercise['title']}", disabled=submit_button_disabled):
                            with st.spinner("Evaluating your code..."):
                                feedback = evaluate_code_with_gemini(user_code, exercise)
                                st.success("Code submitted successfully!")
                                st.markdown("### Feedback")
                                st.markdown(feedback)
        
        # Reflection section - Show after quiz or coding submission
        if st.session_state.show_reflection:
            if st.session_state.current_feedback:
                st.markdown("### Feedback")
                st.write(st.session_state.current_feedback)
            
            st.markdown("### 🤔 Reflection")
            
            # Only show reflection form if reflection hasn't been submitted
            if not st.session_state.reflection_submitted:
                with st.form(key=f"reflection_form_{challenge_id}"):
                    reflection = st.text_area(
                        "What did you learn from this exercise? What was challenging? How confident do you feel about this topic?",
                        key=f"reflection_{challenge_id}"
                    )
                    
                    if st.form_submit_button("Save Reflection", use_container_width=True):
                        if reflection:
                            # Store reflection text
                            st.session_state.reflection_text = reflection
                            
                            # Analyze reflection
                            analysis = analyze_reflection_with_gemini(reflection)
                            
                            # Save reflection to database
                            conn = get_db_connection()
                            cursor = conn.cursor()
                            cursor.execute('''
                            INSERT INTO reflections (user_id, challenge_id, reflection_text, ai_feedback)
                            VALUES (?, ?, ?, ?)
                            ''', (st.session_state.user_id, challenge_id, reflection, 
                                 json.dumps({"code_feedback": st.session_state.current_feedback, 
                                           "reflection_analysis": analysis})))
                            conn.commit()
                            conn.close()

                            # Store analysis in session state
                            st.session_state.reflection_analysis = analysis
                            st.session_state.reflection_submitted = True
                            
                            # Get next level based on reflection
                            next_level = get_next_level(course_id, st.session_state.current_level, analysis["classification"])
                            
                            # Store in session state
                            st.session_state.next_level = next_level
                            st.session_state.show_continue = True
                            st.rerun()
            
            # Show submitted reflection
            if st.session_state.reflection_submitted and st.session_state.reflection_text:
                st.info(st.session_state.reflection_text)
            
            # Show analysis if available
            if st.session_state.reflection_analysis:
                st.write("### 🤖 Analysis of Your Reflection")
                analysis = st.session_state.reflection_analysis
                col1, col2 = st.columns(2)
                
                with col1:
                    difficulty_emoji = "🔴" if analysis["classification"] == "hard" else "🟢"
                    st.markdown(f"**Difficulty Level:** {difficulty_emoji} {analysis['classification'].title()}")
                    st.markdown(f"**Confidence:** {'⭐' * int(analysis['confidence'] * 5)}")
                
                with col2:
                    st.markdown("**Analysis:**")
                    st.info(analysis["reasoning"])
                
                if st.session_state.reflection_analysis["classification"] == "hard":
                    st.warning("Based on your reflection, we'll adjust to some easier exercises to build your confidence! 💪")
                else:
                    st.success("Great work! We'll challenge you with some harder exercises! 🚀")
                
                st.markdown("---")
                
                if st.button("Continue to Next Level", key=f"next_level_{challenge_id}", use_container_width=True):
                    st.session_state.current_level = st.session_state.next_level
                    update_course_progress(st.session_state.user_id, course_id, st.session_state.next_level)
                    
                    # Clear only necessary session states, keep code_states
                    st.session_state.show_reflection = False
                    st.session_state.current_feedback = None
                    st.session_state.submitted_code = None
                    st.session_state.current_exercise = None
                    st.session_state.show_continue = False
                    st.session_state.next_level = None
                    st.session_state.reflection_analysis = None
                    st.session_state.reflection_submitted = False
                    st.session_state.reflection_text = None
                    quiz_state_key = f"quiz_state_{challenge_id}"
                    if quiz_state_key in st.session_state:
                        del st.session_state[quiz_state_key]
                    
                    st.success("Moving to next challenge...")
                    time.sleep(1)
                    st.rerun()
    else:
        st.info("No challenges available for this course yet.")

# Progress Analytics Page
elif selected == "Progress Analytics":
    if st.session_state.white_noise_playing:
        show_white_noise_player("analytics", show_controls=False)
    st.title("📈 Progress Analytics")
    
    if not st.session_state.current_user:
        st.warning("Please set up your profile first!")
        st.stop()
    
    # Get comprehensive analytics data
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Overall progress by course
    cursor.execute('''
    SELECT c.name, up.progress_percentage, up.overall_score, up.status
    FROM courses c
    JOIN user_progress up ON c.id = up.course_id
    WHERE up.user_id = ?
    ''', (st.session_state.user_id,))
    course_progress = cursor.fetchall()
    
    # Quiz performance over time
    cursor.execute('''
    SELECT 
        DATE(qa.completed_at) as date,
        c.name as course_name,
        ROUND(AVG(qa.score), 2) as avg_score,
        COUNT(*) as attempts
    FROM quiz_attempts qa
    JOIN challenges ch ON qa.challenge_id = ch.id
    JOIN courses c ON ch.course_id = c.id
    WHERE qa.user_id = ?
    GROUP BY DATE(qa.completed_at), c.name
    ORDER BY date
    ''', (st.session_state.user_id,))
    quiz_performance = cursor.fetchall()
    
    # Study sessions analysis
    cursor.execute('''
    SELECT DATE(created_at) as date, session_type, COUNT(*) as count, SUM(duration_minutes) as total_minutes
    FROM study_sessions
    WHERE user_id = ?
    GROUP BY DATE(created_at), session_type
    ORDER BY date
    ''', (st.session_state.user_id,))
    study_sessions = cursor.fetchall()
    
    # Reflection count
    cursor.execute('''
    SELECT COUNT(*) FROM reflections WHERE user_id = ?
    ''', (st.session_state.user_id,))
    total_reflections = cursor.fetchone()[0]
    
    conn.close()
    
    # Display analytics
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("📊 Course Progress Overview")
        if course_progress:
            # Create progress chart
            df_progress = pd.DataFrame(course_progress, columns=['Course', 'Progress', 'Score', 'Status'])
            
            fig = px.bar(df_progress, x='Course', y='Progress', 
                        title='Progress by Course', 
                        color='Status',
                        color_discrete_map={'Completed': '#28a745', 'In Progress': '#17a2b8'})
            fig.update_layout(xaxis_tickangle=-45)
            st.plotly_chart(fig, use_container_width=True)
            
            # Progress table
            st.dataframe(df_progress, use_container_width=True)
        else:
            st.info("No course data available yet.")
    
    with col2:
        st.subheader("🎯 Quiz Performance Trends")
        if quiz_performance:
            df_quiz = pd.DataFrame(quiz_performance, columns=['Date', 'Course', 'Average Score', 'Attempts'])
            
            fig = px.line(df_quiz, 
                          x='Date', 
                          y='Average Score',
                          color='Course',
                          title='Quiz Performance Over Time',
                          markers=True)
            fig.update_layout(
                yaxis_range=[0, 100],
                yaxis_title="Score (%)",
                xaxis_title="Date",
                hovermode='x unified'
            )
            st.plotly_chart(fig, use_container_width=True)
            
            # Show attempts table below
            st.markdown("### 📊 Quiz Attempts Details")
            st.dataframe(df_quiz, use_container_width=True)
        else:
            st.info("No quiz data available yet.")
    
    # Study patterns analysis
    st.subheader("⏰ Study Patterns")
    if study_sessions:
        df_study = pd.DataFrame(study_sessions, columns=['Date', 'Type', 'Count', 'Minutes'])
        
        # Pivot for better visualization
        study_pivot = df_study.pivot_table(values='Minutes', index='Date', columns='Type', fill_value=0).reset_index()
        
        if 'Work' in study_pivot.columns:
            fig = px.area(study_pivot, x='Date', y='Work', 
                         title='Study Time (Minutes per Day)')
            st.plotly_chart(fig, use_container_width=True)
        
        # Weekly summary
        st.subheader("📅 This Week's Summary")
        col1, col2, col3, col4 = st.columns(4)
        
        # Calculate weekly stats
        import datetime
        week_ago = datetime.datetime.now() - datetime.timedelta(days=7)
        recent_sessions = [s for s in study_sessions if s[0] >= week_ago.strftime('%Y-%m-%d')]
        
        total_sessions = len(recent_sessions)
        total_minutes = sum([s[3] for s in recent_sessions])
        avg_daily = total_minutes / 7 if total_minutes > 0 else 0
        
        with col1:
            st.metric("Total Sessions", total_sessions)
        with col2:
            st.metric("Total Minutes", total_minutes)
        with col3:
            st.metric("Daily Average", f"{avg_daily:.1f} min")
        with col4:
            st.metric("Reflections", total_reflections)
    else:
        st.info("No study session data available yet. Start using the timer!")
    
    # Achievement badges
    st.subheader("🏆 Achievements")
    
    achievements = []
    if total_reflections >= 5:
        achievements.append("🤔 Thoughtful Learner - 5+ Reflections")
    if course_progress and any(p[1] >= 50 for p in course_progress):
        achievements.append("📚 Half Way There - 50% Progress")
    if course_progress and any(p[3] == 'Completed' for p in course_progress):
        achievements.append("🎓 Course Completer")
    if len([s for s in study_sessions if s[0] >= (datetime.datetime.now() - datetime.timedelta(days=7)).strftime('%Y-%m-%d')]) >= 5:
        achievements.append("🔥 Weekly Warrior - 5+ Sessions")
    
    if achievements:
        for achievement in achievements:
            st.success(achievement)
    else:
        st.info("Keep learning to unlock achievements!")

# Study Timer Page
elif selected == "Study Timer":
    st.title("⏰ Pomodoro Study Timer")
    
    # Enhanced timer with statistics
    col1, col2 = st.columns([2, 1])
    
    with col1:
        show_pomodoro_timer()
    
    with col2:
        st.subheader("🎵 Study Ambience")
        show_white_noise_player("timer_quick", show_controls=True)
        
       

# Footer
st.markdown("---")
st.markdown("""
<div style="text-align: center; padding: 2rem; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 10px; margin-top: 2rem;">
    <h3 style="color: white; margin: 0;">🚀 FocusMate Learning Management System</h3>
    <p style="color: white; margin: 0.5rem 0;">Empowering learners with personalized, interactive education</p>
    <p style="color: rgba(255,255,255,0.8); margin: 0; font-size: 0.9rem;">Track • Learn • Grow • Achieve</p>
</div>
""", unsafe_allow_html=True)