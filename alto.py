from fastapi import FastAPI, WebSocket, Query, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
import uuid
import os
import uvicorn
from dotenv import load_dotenv
from pydantic import BaseModel
import google.generativeai as genai
import random

# 🔹 Load environment variables
load_dotenv()

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Change to "*" to allow all
    allow_credentials=True,
    allow_methods=["*"],  # Allow all HTTP methods (GET, POST, etc.)
    allow_headers=["*"],  # Allow all headers
)
# 🔹 Configure Google Gemini AI
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

# 🔹 PostgreSQL Connection Details (Supabase)
DB_CONFIG = {
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "dbname": os.getenv("DB_NAME"),
}

class UserRequest(BaseModel):
    user_id: str

# ✅ Connect to PostgreSQL
def get_db_connection():
    try:
        return psycopg2.connect(**DB_CONFIG)
    except psycopg2.Error as e:
        print(f"❌ Database connection failed: {e}")
        return None

# ✅ Create necessary tables
def create_tables():
    conn = get_db_connection()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE EXTENSION IF NOT EXISTS "uuid-ossp";  -- ✅ Ensure UUID functions exist

                CREATE TABLE IF NOT EXISTS users (
                    user_id UUID PRIMARY KEY  -- ✅ Ensure user_id is unique
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    session_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),  -- ✅ Use uuid_generate_v4()
                    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    created_at TIMESTAMP DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id SERIAL PRIMARY KEY,
                    session_id UUID NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    message TEXT NOT NULL,
                    role VARCHAR(10) CHECK (role IN ('user', 'bot')),
                    timestamp TIMESTAMP DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    tracking_number TEXT,
                    carrier TEXT,
                    delivery_status TEXT,
                    estimated_delivery TEXT,
                    last_location TEXT
                );
            """)
            conn.commit()
            print("✅ Database tables are ready.")
    except Exception as e:
        print(f"❌ Error creating tables: {e}")
    finally:
        conn.close()

@app.post("/new_chat")
async def new_chat(user: UserRequest):
    conn = get_db_connection()
    if conn is None:
        return {"status": "error"}

    try:
        with conn.cursor() as cur:
            # Insert user if not exists
            cur.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT DO NOTHING;", (user.user_id,))
            # Insert session and get the session ID
            cur.execute("INSERT INTO sessions (user_id) VALUES (%s) RETURNING session_id;", (user.user_id,))
            session_id = cur.fetchone()[0]
            conn.commit()
        return {"status": "success", "session_id": session_id}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()

# ✅ Get Order Details
def get_shipment(order_id):
    conn = get_db_connection()
    if conn is None:
        return None

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM orders WHERE order_id = %s;", (order_id,))
            order = cur.fetchone()
            if order:
                return {
                    "order_id": order[0],
                    "tracking_number": order[1],
                    "carrier": order[2],
                    "delivery_status": order[3],
                    "estimated_delivery": order[4],
                    "last_location": order[5],
                }
    except Exception as e:
        print(f"❌ Error retrieving order: {e}")
    finally:
        conn.close()

    return None

# ✅ Get Chat Messages
def get_conversation(session_id):
    conn = get_db_connection()
    if conn is None:
        return [], None  # Return empty list and no order ID

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT message, role FROM messages WHERE session_id = %s ORDER BY timestamp;", (session_id,))
            result = cur.fetchall()
            
            conversation = [(msg[0], msg[1]) for msg in result] if result else []

            # 🔹 Extract last mentioned order ID from conversation
            last_order_id = None
            for msg, role in reversed(conversation):
                if msg.isdigit():  # Assuming order IDs are numeric
                    last_order_id = msg
                    break
            
            return conversation, last_order_id
    except Exception as e:
        print(f"❌ Error retrieving messages: {e}")
        return [], None
    finally:
        conn.close()


# ✅ Save Chat Messages
def save_conversation(session_id, user_message, bot_message):
    conn = get_db_connection()
    if conn is None:
        return

    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO messages (session_id, message, role) VALUES (%s, %s, 'user');", (session_id, user_message))
            cur.execute("INSERT INTO messages (session_id, message, role) VALUES (%s, %s, 'bot');", (session_id, bot_message))
            conn.commit()
    except Exception as e:
        print(f"❌ Error saving messages: {e}")
    finally:
        conn.close()

def generate_response(session_id, user_query, language="en", student_data=None):
    conversation_history, _ = get_conversation(session_id)

    greetings = ["hello", "hi", "hey", "good morning", "good evening", "howdy"]
    farewells = ["bye", "goodbye", "see you", "take care"]

    if user_query.lower() in greetings:
        return random.choice([
            "Hi, this is Adya from ABC University. It’s great to connect with you! 😊",
            "Hello! I’m Adya, your student assistant from ABC University. Excited to chat!",
        ])

    if user_query.lower() in farewells:
        return random.choice([
            "It was lovely speaking with you. Hope to welcome you to ABC University soon!",
            "Thanks for your time! Wishing you all the best in your journey ahead. 😊"
        ])

    # Short contextual label (replace order with interest-based info if needed)
    context = ""
    if student_data:
        context += f"""
        🎓 **Student Interest:** {student_data.get('program_interest')}
        🕒 **Course Duration:** {student_data.get('duration')}
        📘 **Highlights:** {student_data.get('highlights')}
        ✅ **Eligibility:** {student_data.get('eligibility')}
        💼 **Career Outcomes:** {student_data.get('careers')}
        🎁 **Scholarships & Support:** {student_data.get('support')}
        """

    formatted_history = "\n".join(
        [f"{role.capitalize()}: {msg}" for msg, role in conversation_history]
    )

    prompt = f"""
    You are Adya, a warm and engaging AI voice assistant from ABC University.

    You are calling prospective students to guide them through:
    - Academic programs
    - Scholarships
    - Hostel & campus life
    - Application process

    🔹 Speak in short, friendly, and natural phrases — as if you're on a voice call.
    🔹 Never sound robotic. Be like a friendly counselor aged 25–30.
    🔹 Keep answers under 50 words unless more is asked.
    🔹 Smile through your voice. Avoid repeating the student’s question.

    If the student sounds confused, offer to explain briefly and helpfully.
    If you don't know something, say: “That’s a great question. Let me check and get back to you.”

    ### Past Chat
    {formatted_history}

    ### Student Said
    "{user_query}"

    ### Context
    {context}

    Now generate a response as Adya on the call.
    """

    try:
        response = genai.GenerativeModel("gemini-1.5-flash").generate_content(prompt)
        save_conversation(session_id, user_query, response.text)
        return response.text
    except Exception as e:
        print(f"Gemini error: {e}")
        return "I’m really sorry, something went wrong. Let me get back to you shortly."


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    while True:
        try:
            data = await websocket.receive_json()
            session_id = data.get("session_id")
            user_message = data.get("message", "").strip()
            language = data.get("language", "en")

            if not user_message: 
                continue  

            if not session_id:
                session_id = str(uuid.uuid4())

            conversation_history, last_order_id = get_conversation(session_id)

            # 🔹 Detect if user is referring to a previous order
            order_data = None
            if user_message.isdigit():
                order_data = get_shipment(user_message)
            elif last_order_id:
                order_data = get_shipment(last_order_id)

            # 🔹 Generate response with context
            ai_response = generate_response(session_id, user_message, language, order_data)

            # Send the AI response back to the client
            await websocket.send_json({"response": ai_response})

        except WebSocketDisconnect:
            print("❌ WebSocket Disconnected")
            break  # Exit the loop on disconnect

        except Exception as e:
            print(f"❌ WebSocket Error: {e}")

# ✅ API Root Endpoint
@app.get("/")
def read_root():
    return {"message": "🚀 FastAPI AI Logistics Assistant is running!"}

# ✅ Start the server
if __name__ == "__main__":
    create_tables()
    uvicorn.run("alto:app", host="127.0.0.1", port=8000, reload=True)
