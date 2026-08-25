package com.acme.employees;

/** Employee domain object for the Java reporting importer. */
public class Employee {
    private final String name;
    private final String email;

    public Employee(String name, String email) {
        this.name = name;
        this.email = email;
    }

    public String getName() {
        return name;
    }

    public String getEmail() {
        return email;
    }
}
