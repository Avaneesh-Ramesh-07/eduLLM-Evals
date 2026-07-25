# Human Grading Packet grader_05

For each criterion, enter `P` if the tutor response satisfies it and `F` if it does not.
Use the companion CSV as the official grading sheet. Leave brief notes only when useful.

## grader_05_item_01

- Scenario ID: `tb_0012`
- Use case: `adaptive_explanation`
- Subject: `biology`
- Tutor: `Tutor A`

### Scenario Prompt

I'm still a bit confused about how you ruled out autosomal recessive inheritance. If II-2 is an unaffected female but has an affected son (III-3), couldn't she be a carrier for an autosomal recessive trait and pass it to her child? What exactly makes autosomal recessive inheritance not possible in this pedigree?

### Conversation Context

**Turn 1 (student)**

In a three-generation pedigree showing the inheritance of a specific trait, an unaffected male (I-1) and an affected female (I-2) produce three offspring: II-2 (unaffected female), II-3 (affected male), and II-4 (affected male). II-2 mates with an unaffected male (II-1), producing three offspring: III-1 (affected male), III-2 (unaffected female), and III-3 (affected male). II-4 mates with an affected female (II-5), producing three affected offspring: III-4 (male), III-5 (male), and III-6 (female). What is the inheritance mode of the trait in question?

**Turn 2 (tutor)**

# Analyzing the Inheritance Pattern

To determine the inheritance mode, I'll analyze how this trait is passed through the family.

## Key Observations:
1. An affected female (I-2) has both affected sons (II-3, II-4) and an unaffected daughter (II-2)
2. The unaffected daughter (II-2) has an affected son (III-3)
3. When an affected male (II-4) mates with an affected female (II-5), all their children are affected

## Analysis of Possible Inheritance Modes:

- **Autosomal dominant**: Ruled out because II-2 is unaffected but has an affected child (III-3)
- **Autosomal recessive**: Ruled out because the affected female (I-2) had affected children with an unaffected male
- **X-linked dominant**: Ruled out because II-2 is unaffected despite having an affected mother

- **X-linked recessive**: This fits our observations:
  - Affected females (I-2) pass the trait to sons (II-3, II-4)
  - Unaffected female II-2 must be a carrier and passes the trait to her son (III-3)
  - When both parents have the trait (II-4 and II-5), all offspring are affected

The pattern is consistent with **X-linked recessive inheritance**.

### Reference Solution

# Analyzing the Inheritance Pattern

To determine the inheritance mode, I'll analyze how this trait is passed through the family.

## Key Observations:
1. An affected female (I-2) has both affected sons (II-3, II-4) and an unaffected daughter (II-2)
2. The unaffected daughter (II-2) has an affected son (III-3)
3. When an affected male (II-4) mates with an affected female (II-5), all their children are affected

## Analysis of Possible Inheritance Modes:

- **Autosomal dominant**: Ruled out because II-2 is unaffected but has an affected child (III-3)
- **Autosomal recessive**: Ruled out because the affected female (I-2) had affected children with an unaffected male
- **X-linked dominant**: Ruled out because II-2 is unaffected despite having an affected mother

- **X-linked recessive**: This fits our observations:
  - Affected females (I-2) pass the trait to sons (II-3, II-4)
  - Unaffected female II-2 must be a carrier and passes the trait to her son (III-3)
  - When both parents have the trait (II-4 and II-5), all offspring are affected

The pattern is consistent with **X-linked recessive inheritance**.

### Tutor Response

You’re right to question that: **autosomal recessive inheritance is not strictly impossible from this pedigree as described.** My earlier statement ruling it out was too strong.

## How autosomal recessive *could* fit

For an autosomal recessive trait, affected individuals are **aa**, while unaffected carriers are **Aa**.

### Generation I
- I-2 is affected female → **aa**
- I-1 is unaffected male but has affected children with I-2 → he could be a carrier, **Aa**

Cross:

\[
aa \times Aa
\]

Possible children:
- 50% **aa** affected
- 50% **Aa** unaffected carriers

This can produce:
- II-2 unaffected female → **Aa**
- II-3 affected male → **aa**
- II-4 affected male → **aa**

So far, autosomal recessive works.

### II-2’s children
II-2 is unaffected but could be a carrier, **Aa**.

She mates with II-1, an unaffected male. For them to have affected children, II-1 would also need to be a carrier, **Aa**.

Cross:

\[
Aa \times Aa
\]

Possible children:
- 25% **aa** affected
- 50% **Aa** carriers
- 25% **AA** unaffected non-carriers

So affected children III-1 and III-3 are possible.

### II-4 and II-5
II-4 is affected → **aa**  
II-5 is affected → **aa**

Cross:

\[
aa \times aa
\]

All children would be affected, matching III-4, III-5, and III-6.

So yes: **autosomal recessive inheritance is compatible with the pedigree.**

## Why X-linked recessive is often favored

The pedigree also fits **X-linked recessive inheritance** very well:

- An affected female I-2 with an unaffected male I-1 would have:
  - all sons affected
  - all daughters unaffected carriers

That exactly matches:
- II-3 and II-4 affected sons
- II-2 unaffected daughter

Then II-2, as a carrier female, can have affected sons with an unaffected male, which explains III-1 and III-3.

So the pattern is very characteristic of **X-linked recessive inheritance**, especially because affected individuals are mostly male and carrier females can be unaffected.

## Bottom line

Nothing in the pedigree absolutely excludes autosomal recessive inheritance. The best answer is:

**The trait is most consistent with X-linked recessive inheritance, but autosomal recessive inheritance is also possible unless additional information rules it out.**

If the pedigree question expects a single answer, it is probably aiming for **X-linked recessive**, but your reasoning about autosomal recessive carriers is correct.

### Criteria To Grade

#### tb_0012_c01

- Criterion: The response must explicitly acknowledge the student's stated confusion and respond empathetically before giving the explanation
- Primary skill: ``
- Criticality: `critical`
- Grade (P/F): F
- Notes: ____

#### tb_0012_c02

- Criterion: The response must clearly state that autosomal recessive inheritance is highly improbable (very unlikely) in this pedigree compared with X-linked recessive inheritance.
- Primary skill: `content`
- Criticality: `critical`
- Grade (P/F): F
- Notes: doesn't say this properly; hints at X-linked recessive being more likely, but doesn't say anything about autosomal recessive inheritance being highly improbable (tries to find ways to make it work)

#### tb_0012_c03

- Criterion: The response must explain, step-by-step, why AR is highly improbable, citing at least one key distinguishing pattern (e.g., 100 % affected sons of an affected female, absence of affected daughters in that cross, or the need for two independent carrier marriages).
- Primary skill: `content`
- Criticality: `critical`
- Grade (P/F): F
- Notes: The response shows why AR could be possible, but that X-linked is a closer match, as opposed to explaining why AR is improbable.

#### tb_0012_c04

- Criterion: The response must positively reinforce what the student already got right (that an unaffected female carrier could have affected sons in an AR scenario).
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0012_c05

- Criterion: The response must supply the missing background contrast in transmission probabilities between AR (50 % risk per child) and XLR (100 % risk to sons of an affected female).
- Primary skill: `content`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0012_c06

- Criterion: The response should use clear, student-friendly language and an encouraging tone.
- Primary skill: ``
- Criticality: `not_critical`
- Grade (P/F): P
- Notes: ____

#### tb_0012_c07

- Criterion: The response should employ structured formatting (bullet points, numbering, or headings) to organize ideas.
- Primary skill: ``
- Criticality: `not_critical`
- Grade (P/F): P
- Notes: ____

## grader_05_item_02

- Scenario ID: `tb_0340`
- Use case: `feedback`
- Subject: `statistics`
- Tutor: `Tutor C`

### Scenario Prompt

Okay, I need to find the residual for this elk. I think residual means how far off the prediction is from the actual value, like the difference between predicted and actual weight.

First, I'll plug in the chest circumference into the regression equation:

Predicted weight = -350.3 + 3.7455 x 145 .9

Predicted weight = -350.3 + 546.46

predicted weight = 196.16 kg

Now for the residual, I think it's predicted - actual.

So the residual is like -8.14 kg.

That means the prediction was 8.14 kg more than the actual weight, right?

But wait, I'm not totally sure if it's supposed to be predicted like a - actual or actual um. I went with predicted - actual because that's the error, like what the prediction got wrong. if I got the sign backward then like maybe it just means the elk weighed more than expected?

### Conversation Context

**Turn 1 (student)**

Following is the equation of the least-squares regression line relating chest circumference and weight for male tule elk. 

predicted weight = -350.3 + 3.7455(chest circumference)

The weight of one male tule elk with a chest circumference of 145.9 cm is 204.3 kg. Calculate the residual for this male elk, making sure to show your work along the way. 

### Reference Solution



### Tutor Response

You did an excellent job calculating the predicted weight! Your math for that step is spot on. 

However, you have the formula for the residual slightly backward. 

In statistics, the formula for a residual is always **Actual minus Predicted** (a helpful way to remember this is the acronym **AP**: **A**ctual – **P**redicted).

$$\text{Residual} = y - \hat{y}$$

Here is the correct step-by-step calculation and explanation:

### Step 1: Find the Predicted Weight ($\hat{y}$)
Using the regression equation with the chest circumference of $145.9\text{ cm}$:
$$\text{Predicted weight} = -350.3 + 3.7455(145.9)$$
$$\text{Predicted weight} = -350.3 + 546.4685$$
$$\text{Predicted weight} \approx 196.17\text{ kg}$$

### Step 2: Calculate the Residual
Using the formula $\text{Residual} = \text{Actual} - \text{Predicted}$:
*   **Actual weight ($y$):** $204.3\text{ kg}$
*   **Predicted weight ($\hat{y}$):** $196.17\text{ kg}$

$$\text{Residual} = 204.3 - 196.17$$
$$\text{Residual} = 8.13\text{ kg}$$ *(or $8.1315\text{ kg}$ if you do not round the predicted weight first)*

---

### What does this sign mean?
Because the residual is **positive ($+8.13\text{ kg}$)**, it means the actual elk is **heavier** than the model predicted. 

*   **Positive residual:** The actual value is *above* the regression line (the model underpredicted).
*   **Negative residual:** The actual value is *below* the regression line (the model overpredicted).

In this case, this specific male tule elk weighs **$8.13\text{ kg}$ more** than predicted by the regression line.

### Criteria To Grade

#### tb_0340_c01

- Criterion: The response must include that the student's formula formula, "predicted - actual," is incorrect.
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0340_c02

- Criterion: The response must include that the student's final answer, "-8.14," is incorrect.
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0340_c03

- Criterion: The response must include that the student makes the correct assumption about their answer of "-8.14" that "if I got the sign backward then like maybe it just means the elk weighed more than expected?", because +8.14 is the correct answer and the elk weigh more than expected in this situation.
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): F
- Notes: the only comment on the student's work is to say that their formula was incorrect

#### tb_0340_c04

- Criterion: The response must include that the student's words and their numerical answer don't align because they expressed verbally that the elk "weighs more," but their numerical answer is negative.
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): F
- Notes: the only comment on the student's work is to say that their formula was incorrect

#### tb_0340_c05

- Criterion: The response must include the correct formula for the answer: Residual Weight = (Weighed Elk) - (Predicted Weight) = 204.3 kg - 196.16 kg = 8.14 kg.
- Primary skill: `content`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0340_c06

- Criterion: The response must include that the answer is positive because the elk from the problem (204.3 kg) weighs more than the expected value (196.16 kg).
- Primary skill: `content`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0340_c07

- Criterion: The response must include that the student seemed confused as they explained their negative answer: "...maybe it just means the elk weighed more than expect."
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): F
- Notes: the only comment on the student's work is to say that their formula was incorrect

#### tb_0340_c08

- Criterion: The response must include a validation for the student's question: "That means the prediction was 8.14 kg more than the actual weight, right?" similar to: "Totally! But does that align with what we are actually trying to do in the problem?"
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): F
- Notes: It doesn't address the student's error by asking this follow up question; it just goes into the correct formula

#### tb_0340_c09

- Criterion: The response should include an analogy for thinking about finding residual weight similar to finding the difference between a cookie recipe's expected yield and how many cookies are actually baked.
- Primary skill: `scaffolding`
- Criticality: `not_critical`
- Grade (P/F): F
- Notes: ____

#### tb_0340_c10

- Criterion: The response should be written  from the perspective of the teacher with the use of "you" and "we."
- Primary skill: ``
- Criticality: `not_critical`
- Grade (P/F): P
- Notes: ____

## grader_05_item_03

- Scenario ID: `tb_0335`
- Use case: `feedback`
- Subject: `computer_science`
- Tutor: `Tutor B`

### Scenario Prompt

Okay, why is the variable declared as A obj=new B(), instead of just B obj= new B()? What's the point of using the parent class type? shouldn't this be the correct solution?

public class Main {

      public static void main(String \[\] args) {

          B obj = new B(); 

         obj.reveal();

       }

}

### Conversation Context

**Turn 1 (student)**

```java
class A {
  private void secret() {
    System.out.println("A secret");
  }
  public void reveal() {
    secret();
  }
}

class B extends A {
  private void secret() {
    System.out.println("B secret");
  }
}

public class Main {
  public static void main(String[] args) {
    A obj = new B();
    obj.reveal();
  }
}
```
What is the output of this code?

### Reference Solution



### Tutor Response

# Output

```
A secret
```

## Why "A secret" and not "B secret"?

This is the key lesson of this example: **private methods are NOT polymorphic**. They are not overridden—they are **hidden**.

Because `secret()` is `private`:
- It's not visible to subclasses, so `B.secret()` doesn't actually *override* `A.secret()`.
- Method calls to `private` methods are resolved at **compile time** (static binding), not runtime.
- Inside `A.reveal()`, the call `secret()` is *bound* to `A.secret()` at compile time—no matter what the actual object type is.

So even though `obj` points to a `B` object, `reveal()` calls `A`'s version.

⚠️ If you made `secret()` **public** or **protected**, it WOULD be overridden, and the output would be `B secret`.

---

## Now, your actual question: `A obj = new B()` vs `B obj = new B()`

Both compile and run fine here! Your version is **not wrong**. But there's an important conceptual reason for using the parent type.

### The point of `A obj = new B()`

This is called **programming to an interface/supertype**. It means:
- The **reference type** (`A`) determines what methods you're *allowed* to call.
- The **object type** (`B`) determines which version *runs* (for real polymorphic methods).

### Why this matters in practice

Imagine a method that works with *any* subclass of `A`:

```java
void process(A obj) {
    obj.reveal();
}
```

You can pass a `B`, `C`, `D`... anything that extends `A`. The code doesn't care about the specific subclass. This gives you **flexibility**:

```java
A obj;
if (someCondition)
    obj = new B();
else
    obj = new C();   // could be different subclasses

obj.reveal();  // works regardless
```

With `B obj = new B()`, you lock yourself into `B` and lose that flexibility.

---

## Summary

| Version | Valid? | When to use |
|---------|--------|-------------|
| `B obj = new B()` | ✅ Yes | When you need `B`-specific methods, or don't need flexibility |
| `A obj = new B()` | ✅ Yes | When you want to treat objects generically (polymorphism) |

**But note:** In *this specific example*, the choice of reference type makes **no difference to the output**—because `secret()` is private and never polymorphic. The output stays `A secret` either way.

This example was designed to teach the "private methods aren't overridden" gotcha, and the `A obj = new B()` setup is just the classic style used to *demonstrate* polymorphism (or in this case, its absence).

### Criteria To Grade

#### tb_0335_c01

- Criterion: The response must clearly explain that private methods are resolved based on the class where the method is defined, not the runtime type of the object, and must directly refute the idea that the secret() method from class B is ever called.
- Primary skill: `content`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0335_c02

- Criterion: The explanation must explicitly note that the secret() method in class B is not overriding the secret() method in class A, but instead defines a completely separate method.
- Primary skill: `content`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0335_c03

- Criterion: The response must clarify that reveal() is defined in class A and therefore always executes A's version of secret(), regardless of the runtime type of obj
- Primary skill: `content`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0335_c04

- Criterion: The response must correctly identifies that private methods are not inherited therefore are not overridden.
- Primary skill: `content`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0335_c05

- Criterion: The response must avoids describing the program as "failing to compile" based solely on private method naming overlap.
- Primary skill: `content`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0335_c06

- Criterion: The explanation must clarify that the method call to secret() is resolved at compile time based on the reference type (A), not the runtime type (B), and that polymorphism does not apply to private methods.
- Primary skill: `content`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0335_c07

- Criterion: The response must explicitly state that a private method can be called only by other methods within its own class.
- Primary skill: `content`
- Criticality: `critical`
- Grade (P/F): F
- Notes: ____

#### tb_0335_c08

- Criterion: The model's response should be written in the second person, addressed to the student.
- Primary skill: ``
- Criticality: `not_critical`
- Grade (P/F): P
- Notes: ____

## grader_05_item_04

- Scenario ID: `tb_0497`
- Use case: `hint_generation`
- Subject: `calculus`
- Tutor: `Tutor A`

### Scenario Prompt

### Step 1: Find the derivative

$f'(x) = \\frac{d}{dx} 8x^3 + 4x^2 - 10x + 6$\
$f'(x) = 24x^2 + 8x - 10$

### Step 2: Find the rate of change at x=0.45

$f'(0.45) = 24(0.45)^2 + 8(0.45) - 10$\
$f'(0.45) = -1.54$

### Step 3: Find the y-value at x=0.45

$f(0.45) = 8(0.45)^3 + 4(0.45)^2 - 10(0.45) + 6$\
$f(0.45) = 3.039$

### Step 4: Calculate the y-intercept of the tangent line

$y = mx + b$\
$3.039 = -1.54(0.45) + b$\
$b = 3.039 + 1.54(0.45)$\
$b = 3.732$

### Step 5: Find the equation of the tangent line

$m = -1.54$\
$b = 3.732$\
$y = -1.54x + 3.732$

### Step 6: Find intersection points

$8x^3 + 4x^2 - 10x + 6 = -1.54x + 3.732$\
$8x^3 + 4x^2 - 10x + 6 + 1.54x - 3.732 = 0$\
$8x^3 + 4x^2 - 8.46x + 2.268 = 0$

### Step 7: Find the solutions

x=0.45 is a solution\
$(8x^3 + 4x^2 - 8.46x + 2.268)/(x-0.45)$\
$8x^2+7.6x-5.04$

I'm frustrated because this doesn't seem right. I think I'm doing this wrong because there are too many decimals. 

### Conversation Context

**Turn 1 (student)**

At what other coordinate(s) does the tangent line of curve $f(x) = 8x^3 +4x^2 - 10x + 6$ at $x = 0.45$ intersect with $f(x)$, aside from the point of tangency? 

### Reference Solution



### Tutor Response

You’re not doing it wrong — your work is correct up through the quotient. The key is that since the line is **tangent** at \(x=0.45\), the intersection equation has \(x=0.45\) as a **double root**, so you need to divide by \((x-0.45)\) twice.

You got:

\[
8x^3+4x^2-8.46x+2.268=0
\]

and after dividing by \(x-0.45\):

\[
8x^2+7.6x-5.04
\]

Now factor/divide again:

\[
8x^2+7.6x-5.04=(x-0.45)(8x+11.2)
\]

So the roots are:

\[
x=0.45
\]

again, and

\[
8x+11.2=0
\]

\[
x=-1.4
\]

Now find the corresponding \(y\)-value using the tangent line:

\[
y=-1.54x+3.732
\]

At \(x=-1.4\):

\[
y=-1.54(-1.4)+3.732
\]

\[
y=2.156+3.732=5.888
\]

So the tangent line intersects the curve again at:

\[
\boxed{(-1.4,\ 5.888)}
\]

Equivalently, in fractions:

\[
\boxed{\left(-\frac75,\frac{736}{125}\right)}
\]

### Criteria To Grade

#### tb_0497_c01

- Criterion: The response must say the student correctly found the equation of the tangent line at x=0.45 (y=-1.54x+3.732).
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): P
- Notes: maybe doesn't say this explicitly, but says that the existing work is correct which includes finding the tangent line equation

#### tb_0497_c02

- Criterion: The response must say the student correctly calculated (8x^3+4x^2-8.46x+2.268) / (x-0.45) = 8x^2+7.6x-5.04.
- Primary skill: `content`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0497_c03

- Criterion: The response must provide a hint on how to find the solutions for the quadratic function without explicitly saying to use the quadratic formula.
- Primary skill: `scaffolding`
- Criticality: `critical`
- Grade (P/F): F
- Notes: solves it by factoring and doesn't scaffold at all

#### tb_0497_c04

- Criterion: The response must not provide the final correct answer, (-1.4, 5.89).
- Primary skill: `scaffolding`
- Criticality: `critical_negative`
- Grade (P/F): F
- Notes: ____

#### tb_0497_c05

- Criterion: The response should refer to the student in the second person.
- Primary skill: ``
- Criticality: `not_critical`
- Grade (P/F): P
- Notes: ____

#### tb_0497_c06

- Criterion: The response must acknowledge that the student is frustrated, and so it has to provide support by indicating that they are going in the right direction.
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

## grader_05_item_05

- Scenario ID: `tb_0507`
- Use case: `hint_generation`
- Subject: `physics`
- Tutor: `Tutor B`

### Scenario Prompt

# Spring Constant Determination Lab

I remember that when a spring oscillates, the time it takes depends on the mass and the spring constant, but I'm not exactly sure how. I think the period increases with mass? I don't really remember the exact formula, though.

I was thinking that maybe I can time how long it takes for the spring to go up and down once, then try it with different weights. But I'm not sure if that's enough to figure out what the constant $k$ is? Also, do I need to use just the mass, or do I have to include the box it's in too? It's kind of puzzling.

Would it help to plot something like period versus weight? Or maybe displacement? I feel stuck on how exactly to set up the experiment and what graph would give me $k$...

### Conversation Context

**Turn 1 (student)**

A student brings a toy to physics class that was recently given to them by a family member. The toy consists of a box with a spring attached at one end that can have small weights placed inside it and can be hung from a hook in the ceiling. The students want to determine the spring constant of the spring using physics equipment that is available in their lab, including but not limited to a digital scale, stopwatches, meter sticks, force probes, motion detectors, and photo-gates. Using the principles of oscillation, derive an equation for determining the spring constant using values that can be measured by any of the available equipment. Then, explain how the measured information could be graphed to determine the spring constant of this spring toy.

### Reference Solution



### Tutor Response

# Spring Constant Determination Lab

You're on the right track! Let me help you build this up from what you already remember.

## Starting With What You Know

You're correct that **the period increases with mass**. The governing equation for a mass oscillating on a spring is:

$$T = 2\pi\sqrt{\frac{m}{k}}$$

where:
- $T$ = period (time for one complete up-and-down oscillation)
- $m$ = oscillating mass
- $k$ = spring constant (what we want!)

## Deriving a Usable Equation

Timing a single oscillation is a good instinct, but a single measurement won't reliably give you $k$ (one bad measurement ruins everything, and you can't check for errors). Let's rearrange the equation so we can use **multiple measurements** and a graph.

Square both sides to get rid of the square root:

$$T^2 = 4\pi^2 \frac{m}{k}$$

Now solve this into the form of a straight line, $y = (\text{slope})x + b$:

$$T^2 = \left(\frac{4\pi^2}{k}\right)m$$

This is exactly linear! If we let:
- $y = T^2$
- $x = m$
- slope $= \dfrac{4\pi^2}{k}$

## The Graph You Should Make

**Plot $T^2$ (vertical axis) versus $m$ (horizontal axis).**

Do NOT plot period vs. weight directly — that gives a curve (square root shape), which is hard to analyze. Squaring the period makes it a straight line.

The slope of your best-fit line will be:

$$\text{slope} = \frac{4\pi^2}{k}$$

Solving for the spring constant:

$$\boxed{k = \frac{4\pi^2}{\text{slope}}}$$

## Answering Your Question About the Box

**Yes, you must account for the box's mass** — the box oscillates too! You have two clean options:

**Option 1 – Include it directly:** The total oscillating mass is $m = m_{\text{box}} + m_{\text{added weights}}$. Use a **digital scale** to weigh the empty box, then add each weight's mass. Plot $T^2$ vs. total mass.

**Option 2 – Let the graph handle it:** If you only plot $T^2$ vs. the *added* mass, the box's mass just shifts the line up, creating a **y-intercept** — but the *slope is unchanged*. Since $k$ depends only on the slope, you'd still get the correct answer! (This is actually a nice built-in check.)

## Suggested Procedure

1. **Weigh the empty box** with the digital scale.
2. Hang the toy from the hook.
3. Add a known mass, pull the box down slightly, and release it.
4. **Time 10–20 oscillations** with a stopwatch and divide by that number. (Timing many oscillations dramatically reduces reaction-time error compared to timing just one.)
5. Repeat for several different masses (5+ data points).
6. For each trial, calculate $T$, then $T^2$.
7. Plot $T^2$ vs. $m$, draw a best-fit line, and find the slope.
8. Compute $k = \dfrac{4\pi^2}{\text{slope}}$.

## Why This Works Well

- Using **many oscillations** and **many masses** averages out random errors.
- The **linear graph** lets you spot bad data points easily.
- The **units of $k$** come out as N/m (or kg/s²) — you can sanity-check this from the slope units: $\text{s}^2/\text{kg}$, and $4\pi^2$ is unitless, so $k$ has units of $\text{kg/s}^2 = \text{N/m}$. ✓

You've got everything you need — the key insight was squaring the period to turn the relationship into a straight line!

### Criteria To Grade

#### tb_0507_c01

- Criterion: The response must acknowledge the student's expressed feeling of being 'puzzled' or 'stuck'.
- Primary skill: ``
- Criticality: `critical`
- Grade (P/F): F
- Notes: doesn't acknowledge this directly; indirectly encourages the student

#### tb_0507_c02

- Criterion: The response must address the student's direct question about mass by guiding them to consider the total mass of the system (box + weight).
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0507_c03

- Criterion: The response must hint at the concept of linearizing a graph by suggesting the student manipulate the (to be found) equation to match the form y=mx+b.
- Primary skill: `scaffolding`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0507_c04

- Criterion: The response must not provide the specific formula T=2π(sqrt(m/k)) or directly state that the student should plot (T^2) vs. m.
- Primary skill: `scaffolding`
- Criticality: `critical_negative`
- Grade (P/F): F
- Notes: ____

#### tb_0507_c05

- Criterion: The response should validate the student's initial thoughts by praising their idea to think about the relationship between mass/period and graphing (e.g., 'You're on the right track...').
- Primary skill: `diagnosis`
- Criticality: `not_critical`
- Grade (P/F): P
- Notes: ____

#### tb_0507_c06

- Criterion: The response should use formatting (like bullet points or bolding) to separate distinct hints, making them easier for the student to read.
- Primary skill: ``
- Criticality: `not_critical`
- Grade (P/F): P
- Notes: ____

#### tb_0507_c07

- Criterion: The response must provide the key hint to address the forgotten formula by suggesting the student find or refer to a formula sheet.
- Primary skill: `scaffolding`
- Criticality: `critical`
- Grade (P/F): F
- Notes: just gives the formula directly
