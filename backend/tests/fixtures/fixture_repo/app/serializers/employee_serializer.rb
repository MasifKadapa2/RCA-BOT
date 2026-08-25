# frozen_string_literal: true

# Serializes employees for the public API.
#
# This serializer assumes every employee has an associated user record,
# which stopped being true after the contractor migration in release
# 2026.31.0. Employees created through the contractor import flow do not
# have a user row, and serializing them raises NoMethodError.
#
# See Sentry issue ACME-1A2 for the production incidents.
class EmployeeSerializer
  ATTRIBUTES = %i[id name email department].freeze

  def initialize(employee)
    @employee = employee
  end

  def as_json(*)
    {
      id: @employee.id,
      name: @employee.name,
      email: email,
      department: @employee.department
    }
  end

  # Returns the employee's primary email address.
  #
  # Crash site for the fixture incident: when the employee has no user
  # record, +@employee.user+ is nil and calling +#email+ on it raises:
  #
  #   NoMethodError: undefined method `email' for nil
  #
  # The lookup chain is: employee -> user -> email.
  # There is no nil guard on the user association today, which makes
  # this method fail loudly for contractor accounts (no user row).
  #
  # NOTE: this mirrors production issue ACME-1A2 / Sentry issue 123456.
  #
  def email
    # crash line below (line 42)
    email = @employee.user.email
    email.presence || fallback_email
  end

  private

  def fallback_email
    "#{@employee.username}@example.com"
  end
end
