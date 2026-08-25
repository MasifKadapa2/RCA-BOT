# frozen_string_literal: true

# A user account that owns an employee record.
# Contractor employees may NOT have one of these (see EmployeeSerializer).
class User
  attr_reader :email, :username

  def initialize(email:, username:)
    @email = email
    @username = username
  end

  def display_name
    email.split("@").first
  end
end
