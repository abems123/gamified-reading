class User:
    def __init__(self, id, username, full_name, email, hashed_password, coins, xp, membership):
        self.id = id
        self.username = username
        self.full_name = full_name
        self.email = email
        self.hashed_password = hashed_password
        self.coins = coins
        self.xp = xp
        self.membership = membership