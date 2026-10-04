import os
from dotenv import load_dotenv
from flask import Flask, render_template
from db import close_db
from helpers import current_user
from auth.routes import auth_bp
from dashboard.routes import dashboard_bp
from projects.routes import projects_bp
from sponsors.routes import sponsors_bp
from interests.routes import interests_bp
from spaces.routes import spaces_bp
from dms.routes import dms_bp
from extensions import socketio

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")
app.teardown_appcontext(close_db)
app.register_blueprint(auth_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(projects_bp)
app.register_blueprint(sponsors_bp)
app.register_blueprint(interests_bp)
app.register_blueprint(spaces_bp)
app.register_blueprint(dms_bp)
socketio.init_app(app)

import spaces.chat  # registers the chat events
import dms.chat  # registers the DM events

@app.context_processor
def inject_user():
    return {"user": current_user()}

@app.route("/")
def home():
    return render_template("home.html")

if __name__ == "__main__":
    socketio.run(app, debug=True)