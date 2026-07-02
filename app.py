from flask import Flask, render_template

app = Flask(__name__)

@app.route("/reader")
def reader():
    return render_template("reader.html")

@app.route("/")
def index():
    return "Hello from Flask!"

if __name__ == "__main__":
    app.run(debug=True)