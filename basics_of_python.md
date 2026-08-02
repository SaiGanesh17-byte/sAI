Basics of Python
========================
## Variables and Data Types
Python is a dynamically-typed language, which means you don't have to declare the type of a variable before using it.

### Variables
In Python, you can assign a value to a variable using the assignment operator (=).
```python
x = 5  # integer
y = 2.5  # float
name = 'John Doe'  # string
```
## Control Structures
Control structures determine the flow of a program's execution.

### Conditional Statements
Conditional statements are used to execute different blocks of code based on conditions.
```python
x = 5
if x > 10:
    print('x is greater than 10')
else:
    print('x is less than or equal to 10')
```
### Loops
Loops are used to execute a block of code repeatedly for a specified number of times.
```python
fruits = ['apple', 'banana', 'cherry']
for fruit in fruits:
    print(fruit)
```
## Functions
Functions are reusable blocks of code that can be called multiple times from different parts of your program.
```python
def greet(name):
    print('Hello, ' + name + '!')
greet('John Doe')
```
## Error Handling
Error handling is used to handle runtime errors so that the program can continue execution.
```python
try:
    x = 5 / 0
except ZeroDivisionError:
    print('Cannot divide by zero!')
```