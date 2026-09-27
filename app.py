import os
from dotenv import load_dotenv
from flask import Flask, render_template
from db import close_db
from helpers import current_user
from auth.routes import auth_bp

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")
app.teardown_appcontext(close_db)
app.register_blueprint(auth_bp)

@app.context_processor
def inject_user():
    return {"user": current_user()}

@app.route("/")
def home():
    return render_template("home.html")

if __name__ == "__main__":
    app.run(debug=True)