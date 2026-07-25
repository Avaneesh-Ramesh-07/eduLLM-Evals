# Human Grading Packet grader_03

For each criterion, enter `P` if the tutor response satisfies it and `F` if it does not.
Use the companion CSV as the official grading sheet. Leave brief notes only when useful.

## grader_03_item_01

- Scenario ID: `tb_0001`
- Use case: `adaptive_explanation`
- Subject: `chemistry`
- Tutor: `Tutor C`

### Scenario Prompt

It is clear that we can find the ionization constant of HA using the formula 𝐾 𝑎 = [ 𝐻 + ] [ 𝐴 − ] [ 𝐻 𝐴 ] K a ​ = [HA] [H + ][A − ] ​ . However, I am confused about why you calculated the concentration of HA again, since it was already given in the question. Moreover, if we dilute the solution, the concentration of the system decreases — so how do [H⁺] and [A⁻] increase?

### Conversation Context

**Turn 1 (student)**

A 0.100 M solution of weak monoprotic acid (HA) has a pH of 2.87 at \(25^{\circ} C\). Calculate the acid dissociation constant (\(K_a\)) for HA. Then, determine the percent ionization of the acid in this solution. Finally, if the solution is diluted to half its original concentration, predict whether the percent ionization will increase, decrease, or remain the same, and justify your answer using Le Chatelier's principle and equilibrium concepts.

**Turn 2 (tutor)**

# Calculating Ka and Examining Dilution Effects on Ionization

## Part 1: Calculate Ka for HA
Given:
- [HA] = 0.100 M
- pH = 2.87

Step 1: Calculate [H⁺] from pH
[H⁺] = 10^(-pH) = 10^(-2.87) = 1.35 × 10^(-3) M

Step 2: For a monoprotic acid, [H⁺] = [A⁻]
[A⁻] = 1.35 × 10^(-3) M

Step 3: Calculate remaining [HA] at equilibrium
[HA]equilibrium = 0.100 - 0.00135 = 0.0987 M

Step 4: Apply Ka expression
Ka = [H⁺][A⁻]/[HA] = (1.35 × 10^(-3))²/0.0987 = 1.84 × 10^(-5)

## Part 2: Calculate percent ionization
Percent ionization = ([H⁺]/[HA]initial) × 100%
Percent ionization = (1.35 × 10^(-3)/0.100) × 100% = 1.35%

## Part 3: Effect of dilution on percent ionization
The percent ionization will **increase** when diluted.

Explanation:
1. The equilibrium reaction is: HA ⇌ H⁺ + A⁻
2. When diluted, Le Chatelier's principle predicts the equilibrium will shift to counteract the change
3. Dilution reduces concentration of all species, so equilibrium shifts toward the side with more particles (products)
4. For weak acids, the expression Ka = [H⁺][A⁻]/[HA] remains constant
5. When [HA] decreases due to dilution, the equilibrium must shift right (more ionization) to maintain the same Ka value

This is why the percent ionization of weak acids increases with dilution.

### Reference Solution

# Calculating Ka and Examining Dilution Effects on Ionization

## Part 1: Calculate Ka for HA
Given:
- [HA] = 0.100 M
- pH = 2.87

Step 1: Calculate [H⁺] from pH
[H⁺] = 10^(-pH) = 10^(-2.87) = 1.35 × 10^(-3) M

Step 2: For a monoprotic acid, [H⁺] = [A⁻]
[A⁻] = 1.35 × 10^(-3) M

Step 3: Calculate remaining [HA] at equilibrium
[HA]equilibrium = 0.100 - 0.00135 = 0.0987 M

Step 4: Apply Ka expression
Ka = [H⁺][A⁻]/[HA] = (1.35 × 10^(-3))²/0.0987 = 1.84 × 10^(-5)

## Part 2: Calculate percent ionization
Percent ionization = ([H⁺]/[HA]initial) × 100%
Percent ionization = (1.35 × 10^(-3)/0.100) × 100% = 1.35%

## Part 3: Effect of dilution on percent ionization
The percent ionization will **increase** when diluted.

Explanation:
1. The equilibrium reaction is: HA ⇌ H⁺ + A⁻
2. When diluted, Le Chatelier's principle predicts the equilibrium will shift to counteract the change
3. Dilution reduces concentration of all species, so equilibrium shifts toward the side with more particles (products)
4. For weak acids, the expression Ka = [H⁺][A⁻]/[HA] remains constant
5. When [HA] decreases due to dilution, the equilibrium must shift right (more ionization) to maintain the same Ka value

This is why the percent ionization of weak acids increases with dilution.

### Tutor Response

These are two excellent and very common questions in chemistry! Let’s break down both points to clear up the confusion.

---

### 1. Why did we recalculate the concentration of HA? (Initial vs. Equilibrium)

The concentration given in the problem ($0.100\text{ M}$) is the **initial concentration** of the acid (the amount of HA added to the water before any of it starts to break apart). 

However, the acid dissociation constant ($K_a$) is an **equilibrium constant**. This means the formula for $K_a$ must use the concentrations of the species **at equilibrium** (after the acid has finished dissociating).

To see why we subtract, we can look at an **ICE Table** (Initial, Change, Equilibrium):

| Reaction | $\text{HA}$ | $\rightleftharpoons$ | $\text{H}^+$ | $+$ | $\text{A}^-$ |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **I**nitial | $0.100\text{ M}$ | | $0$ | | $0$ |
| **C**hange | $-x$ | | $+x$ | | $+x$ |
| **E**quilibrium | $0.100 - x$ | | $x$ | | $x$ |

From the pH, we found that $x = [\text{H}^+] = 1.35 \times 10^{-3}\text{ M}$. 

Because some of the $\text{HA}$ dissociated to form $\text{H}^+$ and $\text{A}^-$, there is slightly less $\text{HA}$ left at equilibrium than we started with:
$$[\text{HA}]_{\text{equilibrium}} = 0.100 - 0.00135 = 0.0987\text{ M}$$

*(Note: In some introductory chemistry classes, teachers use the approximation $[\text{HA}]_{\text{equilibrium}} \approx [\text{HA}]_{\text{initial}}$ if the percent ionization is very small (under 5%). If we did that here, $K_a$ would be $1.82 \times 10^{-5}$. Subtracting the $x$ value simply gives a more precise, mathematically rigorous answer).*

---

### 2. The Dilution Paradox: How do $[\text{H}^+]$ and $[\text{A}^-]$ "increase" if we dilute the system?

This is a classic point of confusion. To understand this, we must separate **actual concentration** from **percent ionization**.

When you dilute the solution, the **actual concentrations** of $\text{H}^+$ and $\text{A}^-$ do indeed **decrease** because of the extra volume. However, the **percent ionization** (the fraction of the total acid molecules that split apart) **increases**. 

Here is the mathematical and conceptual proof of why this happens:

#### The Mathematical Proof (using the Reaction Quotient, $Q$)
Imagine we have our equilibrium system:
$$K_a = \frac{[\text{H}^+][\text{A}^-]}{[\text{HA}]}$$

Now, we suddenly dilute the solution to **double the volume** (halving all concentrations). Immediately after adding water, before the system has time to adjust, the new temporary concentrations are:
$$[\text{H}^+]_{\text{new}} = \frac{[\text{H}^+]}{2}, \quad [\text{A}^-]_{\text{new}} = \frac{[\text{A}^-]}{2}, \quad [\text{HA}]_{\text{new}} = \frac{[\text{HA}]}{2}$$

Let's plug these new values into the reaction quotient ($Q$) expression to see if we are still at equilibrium:
$$Q = \frac{\left(\frac{[\text{H}^+]}{2}\right) \left(\frac{[\text{A}^-]}{2}\right)}{\left(\frac{[\text{HA}]}{2}\right)} = \frac{1}{2} \left( \frac{[\text{H}^+][\text{A}^-]}{[\text{HA}]} \right) = \frac{1}{2} K_a$$

Because $Q = \frac{1}{2} K_a$, we see that **$Q < K_a$**. 
To restore equilibrium, $Q$ must increase until it equals $K_a$ again. The only way for $Q$ to increase is for the numerator ($[\text{H}^+][\text{A}^-]$) to increase and the denominator ($[\text{HA}]$) to decrease. Therefore, **the reaction must shift to the right (towards products)**.

#### The Conceptual Summary
* **What happens to the actual concentration?** Because of the added water, the final concentration of $\text{H}^+$ at the new equilibrium will still be lower than it was in the concentrated solution.
* **What happens to the percent ionization?** Because the equilibrium shifted to the right to fight the dilution, a **larger percentage** of the remaining $\text{HA}$ molecules were forced to split apart than before. 

**Analogy:** Imagine a crowded dance floor (concentrated solution) where people prefer to dance in pairs ($\text{HA}$). If you suddenly move everyone to a gym that is twice as large (dilution), there is much more space, and more couples will decide to split up and dance individually ($\text{H}^+$ and $\text{A}^-$). The *density* of individual dancers per square foot decreases because the room is so big, but the *percentage* of total people dancing individually increases.

### Criteria To Grade

#### tb_0001_c01

- Criterion: The response must provide the answer for the ionization constant. (e.g., Ka = 1.847 x 10 ^-5)
- Primary skill: `content`
- Criticality: `not_critical`
- Grade (P/F): P
- Notes: ____

#### tb_0001_c02

- Criterion: The response must include the formula to be used to calculate the ionic concentration. (e.g., pH to [H⁺] conversion formula: $[H^+] = 10^{-pH}$)
- Primary skill: `content`
- Criticality: `not_critical`
- Grade (P/F): P
- Notes: ____

#### tb_0001_c03

- Criterion: The Response must explain the Le-Chatelier principle (e.g., the  equilibrium system shifts either to the left or to the right to reduce the change applied)
- Primary skill: `content`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0001_c04

- Criterion: The response must address why we need to calculate HA again even if it is already given (e.g., because ionization is calculated for equilibrium equation HA = H⁺ + A⁻).
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0001_c05

- Criterion: The response should present a step-by-step solution, beginning with the calculation of $[H^+]$, followed by $[A^-]$, then computing the $K_a$ value and percent ionization. It should conclude with an explanation grounded in equilibrium principles.
- Primary skill: `scaffolding`
- Criticality: `not_critical`
- Grade (P/F): P
- Notes: ____

#### tb_0001_c06

- Criterion: The response must provide the answer for the percentage ionization. (e.g. %ionization = 1.35)
- Primary skill: `content`
- Criticality: `not_critical`
- Grade (P/F): P
- Notes: ____

#### tb_0001_c07

- Criterion: The response must include the formula to be used to calculate percentage ionization. (e.g., % ionization = [H⁺] or [A⁻] / [HA] initial × 100)
- Primary skill: `content`
- Criticality: `not_critical`
- Grade (P/F): P
- Notes: ____

#### tb_0001_c08

- Criterion: The response must include the formula used to calculate Ka. (e.g., K_a = \frac{[H^+][A^-]}{[HA]} \]))
- Primary skill: `content`
- Criticality: `not_critical`
- Grade (P/F): P
- Notes: ____

#### tb_0001_c09

- Criterion: The response must provide the answer for the percentage ionization after dilution. (e.g. %ionization = 1.9%)
- Primary skill: `content`
- Criticality: `not_critical`
- Grade (P/F): P
- Notes: ____

#### tb_0001_c10

- Criterion: The model should acknowledge the student's confusion regarding the re-calculation of HA concentration.
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

## grader_03_item_02

- Scenario ID: `tb_0003`
- Use case: `adaptive_explanation`
- Subject: `calculus`
- Tutor: `Tutor A`

### Scenario Prompt

This solution is incorrect. I think first let u=x+y is correct. Then take the derivative with respect to x: \(\frac{du}{dx} = \frac{dy}{dx}\). Substituting, \[
\frac{du}{dx} = \cos(u)
\]. Separating the variables, \[
\frac{du}{\cos(u)} = dx
\]. Integrating both sides of the equation, \(\sin(u) = Cx\). Substituting back u=x+y, y=arcsin(Cx)-x, C is an arbitrary constant. Therefore, I think the answer is y=arcsin(Cx) - x. Could you check my solution and explain it?

### Conversation Context

**Turn 1 (student)**

Solve the differential equation given by $\frac{dy}{dx} = \cos(x+y)$. Carefully show all the necessary steps you use to simplify the equation.

**Turn 2 (tutor)**

# Solving the Differential Equation $\frac{dy}{dx} = \cos(x+y)$

I'll solve this differential equation by using a substitution technique.

## Step 1: Make a substitution
Let's define a new variable $u = x + y$

This means $y = u - x$

Taking the derivative with respect to $x$:
$$\frac{dy}{dx} = \frac{du}{dx} - 1$$

## Step 2: Rewrite the original equation
From our original equation $\frac{dy}{dx} = \cos(x+y)$, we can substitute:
$$\frac{du}{dx} - 1 = \cos(u)$$

Rearranging:
$$\frac{du}{dx} = 1 + \cos(u)$$

## Step 3: Separate variables
$$\frac{du}{1 + \cos(u)} = dx$$

## Step 4: Integrate both sides
$$\int \frac{du}{1 + \cos(u)} = \int dx$$

For the left side, I'll use the identity $1 + \cos(u) = 2\cos^2(u/2)$:

$$\int \frac{du}{2\cos^2(u/2)} = \int dx$$

## Step 5: Further substitution
Let $v = u/2$, so $du = 2dv$:

$$\int \frac{2dv}{2\cos^2(v)} = \int dx$$

$$\int \sec^2(v) dv = \int dx$$

## Step 6: Perform the integration
$$\tan(v) + C_1 = x + C_2$$

Where $C_1$ and $C_2$ are constants of integration. Combining them as $C = C_2 - C_1$:

$$\tan(v) = x + C$$

## Step 7: Substitute back
Substituting $v = u/2$:
$$\tan(u/2) = x + C$$

And finally, substituting $u = x + y$:
$$\tan\left(\frac{x + y}{2}\right) = x + C$$

## Step 8: Solve for $y$ explicitly
$$\frac{x + y}{2} = \arctan(x + C)$$

$$x + y = 2\arctan(x + C)$$

$$y = 2\arctan(x + C) - x$$

Therefore, the general solution to the differential equation $\frac{dy}{dx} = \cos(x+y)$ is:

$$y = 2\arctan(x + C) - x$$

where $C$ is an arbitrary constant.

### Reference Solution

# Solving the Differential Equation $\frac{dy}{dx} = \cos(x+y)$

I'll solve this differential equation by using a substitution technique.

## Step 1: Make a substitution
Let's define a new variable $u = x + y$

This means $y = u - x$

Taking the derivative with respect to $x$:
$$\frac{dy}{dx} = \frac{du}{dx} - 1$$

## Step 2: Rewrite the original equation
From our original equation $\frac{dy}{dx} = \cos(x+y)$, we can substitute:
$$\frac{du}{dx} - 1 = \cos(u)$$

Rearranging:
$$\frac{du}{dx} = 1 + \cos(u)$$

## Step 3: Separate variables
$$\frac{du}{1 + \cos(u)} = dx$$

## Step 4: Integrate both sides
$$\int \frac{du}{1 + \cos(u)} = \int dx$$

For the left side, I'll use the identity $1 + \cos(u) = 2\cos^2(u/2)$:

$$\int \frac{du}{2\cos^2(u/2)} = \int dx$$

## Step 5: Further substitution
Let $v = u/2$, so $du = 2dv$:

$$\int \frac{2dv}{2\cos^2(v)} = \int dx$$

$$\int \sec^2(v) dv = \int dx$$

## Step 6: Perform the integration
$$\tan(v) + C_1 = x + C_2$$

Where $C_1$ and $C_2$ are constants of integration. Combining them as $C = C_2 - C_1$:

$$\tan(v) = x + C$$

## Step 7: Substitute back
Substituting $v = u/2$:
$$\tan(u/2) = x + C$$

And finally, substituting $u = x + y$:
$$\tan\left(\frac{x + y}{2}\right) = x + C$$

## Step 8: Solve for $y$ explicitly
$$\frac{x + y}{2} = \arctan(x + C)$$

$$x + y = 2\arctan(x + C)$$

$$y = 2\arctan(x + C) - x$$

Therefore, the general solution to the differential equation $\frac{dy}{dx} = \cos(x+y)$ is:

$$y = 2\arctan(x + C) - x$$

where $C$ is an arbitrary constant.

### Tutor Response

You are right that the substitution

\[
u=x+y
\]

is the natural first step. However, there are two mistakes in your solution.

## 1. Derivative of \(u=x+y\)

Since

\[
u=x+y,
\]

differentiate both sides with respect to \(x\):

\[
\frac{du}{dx}=\frac{d}{dx}(x+y)=1+\frac{dy}{dx}.
\]

So it is **not**

\[
\frac{du}{dx}=\frac{dy}{dx}.
\]

That missing \(1\) changes the equation.

From the original differential equation,

\[
\frac{dy}{dx}=\cos(x+y),
\]

and since \(u=x+y\), we have

\[
\frac{dy}{dx}=\cos u.
\]

Therefore,

\[
\frac{du}{dx}=1+\frac{dy}{dx}=1+\cos u.
\]

So the correct equation is

\[
\frac{du}{dx}=1+\cos u.
\]

## 2. Separate the variables

Now separate:

\[
\frac{du}{1+\cos u}=dx.
\]

Use the half-angle identity

\[
1+\cos u=2\cos^2\left(\frac u2\right).
\]

Then

\[
\frac{du}{1+\cos u}
=
\frac{du}{2\cos^2(u/2)}
=
\frac12 \sec^2\left(\frac u2\right)\,du.
\]

So

\[
\int \frac{du}{1+\cos u}
=
\int \frac12 \sec^2\left(\frac u2\right)\,du.
\]

Let

\[
v=\frac u2,
\]

so

\[
du=2\,dv.
\]

Then

\[
\int \frac12 \sec^2\left(\frac u2\right)\,du
=
\int \frac12 \sec^2(v)(2\,dv)
=
\int \sec^2(v)\,dv.
\]

Thus

\[
\int \sec^2(v)\,dv=\tan v.
\]

So we get

\[
\tan\left(\frac u2\right)=x+C.
\]

Now substitute back \(u=x+y\):

\[
\boxed{\tan\left(\frac{x+y}{2}\right)=x+C.}
\]

This is a correct implicit form of the solution.

Solving for \(y\),

\[
\frac{x+y}{2}=\arctan(x+C),
\]

so

\[
x+y=2\arctan(x+C),
\]

and therefore

\[
\boxed{y=2\arctan(x+C)-x.}
\]

More generally, because tangent is periodic, one can also write

\[
\boxed{y=2\arctan(x+C)+2k\pi-x}
\]

for any integer \(k\).

There are also constant-\(u\) solutions when

\[
1+\cos u=0,
\]

so

\[
\cos u=-1.
\]

Thus

\[
u=(2k+1)\pi.
\]

Since \(u=x+y\), this gives the additional solutions

\[
\boxed{y=(2k+1)\pi-x.}
\]

## Why your proposed answer is not correct

You proposed

\[
y=\arcsin(Cx)-x.
\]

Then

\[
x+y=\arcsin(Cx).
\]

So

\[
\cos(x+y)=\cos(\arcsin(Cx))=\sqrt{1-C^2x^2}.
\]

But differentiating your \(y\),

\[
\frac{dy}{dx}=\frac{C}{\sqrt{1-C^2x^2}}-1.
\]

These are not equal in general:

\[
\frac{C}{\sqrt{1-C^2x^2}}-1 \neq \sqrt{1-C^2x^2}.
\]

So your solution does not satisfy the original differential equation except possibly at isolated special values.

The main issue was that

\[
\frac{du}{dx}=1+\frac{dy}{dx},
\]

not just \(\frac{dy}{dx}\).

### Criteria To Grade

#### tb_0003_c01

- Criterion: The response must identify that some methods, like substitution (i.e. let u=x+y), variables separation (i.e. f(y)dy=g(x)dx), and take antiderivative (i.e. \[
\int f(y) \, dy = \int g(x) \, dx
\]) should be used to solve the differential equation.
- Primary skill: `content`
- Criticality: `not_critical`
- Grade (P/F): P
- Notes: ____

#### tb_0003_c02

- Criterion: The response must provide the correct answer to the question, i.e. the correct answer is \[ 
y = 2\arctan(x + C) - x 
\].
- Primary skill: `content`
- Criticality: `not_critical`
- Grade (P/F): P
- Notes: ____

#### tb_0003_c03

- Criterion: The response must identify that the student's solution [let u=x+y, \(\frac{du}{dx} = \frac{dy}{dx}\)] is incorrect.
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): F
- Notes: ____

#### tb_0003_c04

- Criterion: The response must correct the error [let u=x+y, \(\frac{du}{dx} = \frac{dy}{dx}\)], i.e. since u=x+y, take derivative respect to x, \(\frac{du}{dx} = \frac{d(x+y)}{dx} = 1 + \frac{dy}{dx}\).
- Primary skill: `diagnosis`
- Criticality: `not_critical`
- Grade (P/F): P
- Notes: ____

#### tb_0003_c05

- Criterion: The response must identify that the antiderivative \[
\int \frac{du}{\cos(u)} = \sin(u)
\] is incorrect.
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): F
- Notes: ____

#### tb_0003_c06

- Criterion: The response must correct error of the antiderivative, i.e. \[
\int \frac{du}{\cos(u)} = \ln \left| \sec(u) + \tan(u) \right| + C
\], and the student probably got wrong by using \[ 
\int \cos(u) \, \mathrm{d}u = \sin(u) + C 
\].
- Primary skill: `content`
- Criticality: `critical`
- Grade (P/F): F
- Notes: ____

#### tb_0003_c07

- Criterion: The response must identify that the antiderivative \(\int dx = Cx\) is incorrect.
- Primary skill: `diagnosis`
- Criticality: `not_critical`
- Grade (P/F): P
- Notes: ____

#### tb_0003_c08

- Criterion: The response must correct error of the antiderivative, i.e. \[ 
\text{int } dx = x + C 
\], .
- Primary skill: `content`
- Criticality: `not_critical`
- Grade (P/F): P
- Notes: ____

#### tb_0003_c09

- Criterion: The response must provide explanations to answer the student's question, i.e. you made 3 errors in you solution, \(\frac{du}{dx} = \frac{dy}{dx}\), \[
\int \frac{du}{\cos(u)} = \sin(u)
\], and \(\int dx = Cx\), these incorrect steps lead to your incorrect final answer [\( y = \arcsin(Cx) - x \)].
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0003_c10

- Criterion: The response should be formatted by using headings, bold text, and correct LaTeX.
- Primary skill: ``
- Criticality: `not_critical`
- Grade (P/F): P
- Notes: ____

## grader_03_item_03

- Scenario ID: `tb_0340`
- Use case: `feedback`
- Subject: `statistics`
- Tutor: `Tutor B`

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

# Finding the Residual

You've got the right idea, but let me help you fix a couple of things—especially the definition of residual!

## Step 1: Calculate the Predicted Weight

Plug the chest circumference (145.9 cm) into the regression equation:

$$\text{predicted weight} = -350.3 + 3.7455(145.9)$$

$$\text{predicted weight} = -350.3 + 546.47$$

$$\text{predicted weight} = 196.17 \text{ kg}$$

✅ Your prediction is correct (small rounding difference).

## Step 2: Use the Correct Residual Formula

Here's the important fix. The residual is defined as:

$$\boxed{\text{residual} = \text{actual} - \text{predicted}}$$

**Not** predicted − actual! This is a common mix-up, so it's great you flagged your uncertainty.

## Step 3: Calculate the Residual

$$\text{residual} = 204.3 - 196.17$$

$$\text{residual} = +8.13 \text{ kg}$$

## Interpreting the Result

Since the residual is **positive**, your instinct in that last sentence was actually the correct interpretation:

> **The elk weighed 8.13 kg *more* than the model predicted.**

## Quick Note on the Sign Convention

The reason we use **actual − predicted** (not the reverse) is so that the sign tells us something intuitive:

| Residual Sign | Meaning |
|---------------|---------|
| **Positive (+)** | Actual value is *above* the line (model **underpredicted**) |
| **Negative (−)** | Actual value is *below* the line (model **overpredicted**) |

So a positive residual = the real data point sits *above* the regression line. 👍

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
- Grade (P/F): F
- Notes: ____

#### tb_0340_c03

- Criterion: The response must include that the student makes the correct assumption about their answer of "-8.14" that "if I got the sign backward then like maybe it just means the elk weighed more than expected?", because +8.14 is the correct answer and the elk weigh more than expected in this situation.
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0340_c04

- Criterion: The response must include that the student's words and their numerical answer don't align because they expressed verbally that the elk "weighs more," but their numerical answer is negative.
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): F
- Notes: ____

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
- Grade (P/F): F
- Notes: ____

#### tb_0340_c07

- Criterion: The response must include that the student seemed confused as they explained their negative answer: "...maybe it just means the elk weighed more than expect."
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0340_c08

- Criterion: The response must include a validation for the student's question: "That means the prediction was 8.14 kg more than the actual weight, right?" similar to: "Totally! But does that align with what we are actually trying to do in the problem?"
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

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

## grader_03_item_04

- Scenario ID: `tb_0335`
- Use case: `feedback`
- Subject: `computer_science`
- Tutor: `Tutor C`

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

Here are the answers to your questions, broken down step-by-step.

---

### 1. What is the output of this code?

The output of the code is:
```text
A secret
```

#### Why? (The "Private Method" Trap)
You might expect it to print `"B secret"` because the actual object created is of type `B` (`new B()`). However, in Java, **`private` methods cannot be overridden**. 

* Since `secret()` in class `A` is `private`, it is invisible to class `B`. 
* Class `B` defines its own `private void secret()`, but this is a completely separate method; it does not override `A`'s method.
* When `obj.reveal()` is called, it executes the `reveal()` method defined in class `A`. 
* Inside `A.reveal()`, the call to `secret()` is resolved at compile-time (static binding) to point to `A`'s own private `secret()` method.

---

### 2. Why declare `A obj = new B();` instead of `B obj = new B();`?

Declaring a variable by its parent type (or interface) is a fundamental concept in Object-Oriented Programming (OOP) called **Polymorphism** (specifically, **Upcasting**). 

While it might seem unnecessary in this tiny example, it is a best practice in real-world software development for several reasons:

#### Reason A: Flexibility and Decoupling
Writing code that relies on the parent class (or interface) makes your code flexible. You can change the underlying implementation later without changing the code that uses it.

**Example:**
Imagine you have a `Printer` class:
```java
class Printer {
    public void printDocument(A obj) { // Accepts A, B, or any future subclass of A
        obj.reveal();
    }
}
```
Because the parameter is of type `A`, this printer can accept `new B()`, `new C()`, or `new D()`. If you had restricted it to `B obj`, the printer would *only* work with `B`.

#### Reason B: Coding to an Interface/Superclass
In Java, you will often see:
```java
List<String> names = new ArrayList<>();
```
Instead of:
```java
ArrayList<String> names = new ArrayList<>();
```
By using `List` (the parent interface), if you decide tomorrow that a `LinkedList` is better for performance, you only have to change one word:
```java
List<String> names = new LinkedList<>(); // The rest of your code remains untouched!
```

---

### 3. Is `B obj = new B();` the "correct" solution?

**No, but it is not "wrong" either. It depends on what you are trying to achieve.**

If you change the code to:
```java
B obj = new B();
obj.reveal();
```
**The output is still exactly the same: `A secret`.** 

This is because `reveal()` is still inherited from `A`, and `A`'s `reveal()` still calls `A`'s private `secret()`. 

#### Why the original code used `A obj = new B();`:
This code is a classic **Java interview/exam question**. The author of the question specifically wrote `A obj = new B();` to test two things at once:
1. Do you understand **Polymorphism** (how Java decides which method to call at runtime)?
2. Do you understand **Method Hiding vs. Overriding** (that `private` methods cannot be overridden)?

If they had written `B obj = new B();`, it would have made the question slightly easier, but the core trick regarding the `private` method would still behave the same way.

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
- Grade (P/F): F
- Notes: ____

#### tb_0335_c04

- Criterion: The response must correctly identifies that private methods are not inherited therefore are not overridden.
- Primary skill: `content`
- Criticality: `critical`
- Grade (P/F): F
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
- Grade (P/F): F
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

## grader_03_item_05

- Scenario ID: `tb_0507`
- Use case: `hint_generation`
- Subject: `physics`
- Tutor: `Tutor A`

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

Yes — the period increases with mass, but not linearly. For a mass on a spring, the period depends on the square root of the mass.

## 1. Derive the oscillation equation

For a vertical spring-mass system, the forces are gravity and the spring force. When the toy is hanging at equilibrium, gravity just stretches the spring to a new equilibrium position. If the toy is then displaced slightly and released, the motion about equilibrium is simple harmonic motion.

The restoring force is

\[
F=-kx
\]

Using Newton’s second law,

\[
F=ma
\]

so

\[
ma=-kx
\]

or

\[
a=-\frac{k}{m}x
\]

This is the standard simple harmonic motion form:

\[
a=-\omega^2 x
\]

so

\[
\omega^2=\frac{k}{m}
\]

Since

\[
\omega=\frac{2\pi}{T}
\]

we get

\[
\left(\frac{2\pi}{T}\right)^2=\frac{k}{m}
\]

Solving for period:

\[
T=2\pi\sqrt{\frac{m}{k}}
\]

Squaring both sides:

\[
T^2=\frac{4\pi^2}{k}m
\]

That is the key equation.

## 2. Include the total oscillating mass

You should include the mass of everything that oscillates:

\[
m_{\text{total}}=m_{\text{box}}+m_{\text{added}}
\]

If the spring itself has noticeable mass, a more careful model includes an effective spring mass, often approximated as

\[
m_{\text{eff}}=m_{\text{box}}+m_{\text{added}}+\frac{1}{3}m_{\text{spring}}
\]

But for many classroom springs, the spring mass is small enough to ignore.

So the useful lab equation is

\[
T^2=\frac{4\pi^2}{k}m_{\text{total}}
\]

or

\[
T^2=\frac{4\pi^2}{k}\left(m_{\text{box}}+m_{\text{added}}\right)
\]

## 3. Experimental procedure

1. Measure the mass of the box and any added weights using a digital scale.
2. Hang the spring toy from the ceiling.
3. Pull it down a small distance and release it so it oscillates vertically.
4. Measure the time for several oscillations, such as 10 or 20 cycles.
5. Find the period:

\[
T=\frac{\text{total time}}{\text{number of oscillations}}
\]

6. Repeat for several different added masses.

Using many oscillations reduces timing error compared with timing only one oscillation.

## 4. Graph to determine the spring constant

Because

\[
T^2=\frac{4\pi^2}{k}m_{\text{total}}
\]

you should graph

\[
T^2 \text{ vs. } m_{\text{total}}
\]

The graph should be linear.

Its slope will be

\[
\text{slope}=\frac{4\pi^2}{k}
\]

Therefore,

\[
k=\frac{4\pi^2}{\text{slope}}
\]

So if you graph \(T^2\) on the vertical axis and total mass on the horizontal axis, the spring constant is found from the inverse of the slope multiplied by \(4\pi^2\).

## 5. If you graph using added mass only

If you only put \(m_{\text{added}}\) on the horizontal axis, then

\[
T^2=\frac{4\pi^2}{k}m_{\text{added}}+\frac{4\pi^2}{k}m_{\text{box}}
\]

This still gives a straight line. The slope is still

\[
\frac{4\pi^2}{k}
\]

so you can still find

\[
k=\frac{4\pi^2}{\text{slope}}
\]

The y-intercept accounts for the mass of the box and any other effective moving mass.

## Final graph choice

The best graph is:

\[
\boxed{T^2 \text{ vs. } m_{\text{total}}}
\]

with

\[
\boxed{k=\frac{4\pi^2}{\text{slope}}}
\]

If mass is measured in kilograms and time in seconds, the spring constant will come out in newtons per meter, \(\text{N/m}\).

### Criteria To Grade

#### tb_0507_c01

- Criterion: The response must acknowledge the student's expressed feeling of being 'puzzled' or 'stuck'.
- Primary skill: ``
- Criticality: `critical`
- Grade (P/F): F
- Notes: ____

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
- Grade (P/F): F
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
- Notes: ____
