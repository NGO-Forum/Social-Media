# Media

This project automates the task of posting the same media content onto multiple socialmedia platforms like facebook.com , twitter.com and instagram.com.
Python, Selenium and Flask are being used to make the magic happen.

<strong>Setup</strong>
<ol>
    <li>cd Automated-Socialmedia-Posting</li>
    <li>pip install -r requirements.txt</li>
    <li>python app.py</li>
</ol>
<strong>Guidelines</strong>
<ul>
    <li>If any code from this project is used, do add my name in the list of contributers.</li>
    <li>Found an issue? Open a new issue if it's not already listed</li>
    <li>Want to contribute to this project? Fork the repo, make changes and then send a pull request.</li>
    <li>TRUNCATE TABLE posts;.</li>
</ul>

# MySQL connection
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:@172.31.21.60/media'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
