# frozen_string_literal: true

class User
  attr_accessor :email

  def initialize(email: nil)
    @email = email
  end
end

class Employee
  attr_reader :name, :user

  def initialize(name, user = nil)
    @name = name
    @user = user
  end
end

# Serializes employees for the API response.
class EmployeeSerializer
  def initialize(employee)
    @employee = employee
  end

  # BUG: employee.user may be nil; calling .email on nil raises NoMethodError
  def serialize
    {
      name: @employee.name,
      email: @employee.user.email
    }
  end

  def serialize_summary
    { name: @employee.name }
  end

  def self.build_employees
    [
      Employee.new('Ada', User.new(email: 'ada@example.com')),
      Employee.new('Bob', nil)
    ]
  end
end
