
def serialize_employee(employee):
    # Bug: employee is None, like the Ruby bug
    return {"email": employee.user.email}

class User:
    email = "test@example.com"

class Emp:
    user = None

if __name__ == "__main__":
    serialize_employee(Emp())
