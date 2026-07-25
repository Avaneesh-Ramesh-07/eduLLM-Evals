# Human Grading Packet grader_02

For each criterion, enter `P` if the tutor response satisfies it and `F` if it does not.
Use the companion CSV as the official grading sheet. Leave brief notes only when useful.

## grader_02_item_01

- Scenario ID: `tb_0001`
- Use case: `adaptive_explanation`
- Subject: `chemistry`
- Tutor: `Tutor B`

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

# Great Questions! Let me clarify both points.

## Question 1: Why recalculate [HA]?

The key distinction here is between **initial** concentration and **equilibrium** concentration.

**What was given:** 0.100 M is the *initial* concentration of HA (before any ionization occurs).

**What Ka needs:** The Ka expression requires *equilibrium* concentrations of all species.

When HA ionizes, some of it breaks apart:

$$HA \rightleftharpoons H^+ + A^-$$

| | HA | H⁺ | A⁻ |
|-----|------|------|------|
| Initial | 0.100 | 0 | 0 |
| Change | −x | +x | +x |
| Equilibrium | 0.100 − x | x | x |

Here, x = [H⁺] = 1.35 × 10⁻³ M

So at equilibrium:
$$[HA]_{eq} = 0.100 - 0.00135 = 0.0987 \text{ M}$$

**Why it matters:** Some HA molecules converted into ions, so there's slightly *less* HA remaining than we started with. Using 0.100 instead of 0.0987 introduces a small error.

**Note:** In this case, since x is tiny compared to 0.100, the approximation [HA] ≈ 0.100 would give nearly the same answer. But using the exact value (0.0987) is more rigorous.

---

## Question 2: Why do [H⁺] and [A⁻] "increase" upon dilution?

This is a subtle but important point. Let me clarify what actually happens.

### Important clarification: The concentrations DON'T increase!

When you dilute, **all concentrations decrease** (including [H⁺] and [A⁻]). I should have been clearer about this earlier.

**What actually increases is the *percent* ionization — not the concentration.**

### What really happens during dilution:

Let's compare before and after diluting to half concentration:

| Quantity | Before dilution | After dilution |
|----------|----------------|----------------|
| [HA] | Higher | **Lower** |
| [H⁺] | Higher | **Lower** |
| [A⁻] | Higher | **Lower** |
| **% ionization** | Lower | **Higher** ✓ |

### So why does % ionization increase?

Think of it as a **competition** between two effects:

$$K_a = \frac{[H^+][A^-]}{[HA]}$$

- The numerator has **two** concentration terms (both decrease)
- The denominator has **one** concentration term (decreases)

To keep Ka constant, the equilibrium must shift **right** (more ionization) to compensate. This means a *larger fraction* of the acid ionizes, even though the absolute amount of ions is smaller.

### An analogy:
Imagine 100 people at a party in a **small room** — they stay clustered together (low % "spread out"). Move them to a **huge hall** — the same 100 people spread out more (higher % "spread out"), even though there are still only 100 people.

Dilution gives ions more "room," favoring the dissociated state, so a **greater percentage** ionizes — but the actual ion concentration still drops.

---

### Summary
- ✅ Concentrations of H⁺ and A⁻ **decrease** upon dilution
- ✅ **Percent ionization increases** upon dilution
- These are consistent because % ionization = (amount ionized) ÷ (total amount), and the total decreases faster than expected

I apologize for the confusing wording in my earlier response!

### Criteria To Grade

#### tb_0001_c01

- Criterion: The response must provide the answer for the ionization constant. (e.g., Ka = 1.847 x 10 ^-5)
- Primary skill: `content`
- Criticality: `not_critical`
- Grade (P/F): F
- Notes: ____

#### tb_0001_c02

- Criterion: The response must include the formula to be used to calculate the ionic concentration. (e.g., pH to [H⁺] conversion formula: $[H^+] = 10^{-pH}$)
- Primary skill: `content`
- Criticality: `not_critical`
- Grade (P/F): F
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
- Grade (P/F): F
- Notes: ____

#### tb_0001_c06

- Criterion: The response must provide the answer for the percentage ionization. (e.g. %ionization = 1.35)
- Primary skill: `content`
- Criticality: `not_critical`
- Grade (P/F): F
- Notes: ____

#### tb_0001_c07

- Criterion: The response must include the formula to be used to calculate percentage ionization. (e.g., % ionization = [H⁺] or [A⁻] / [HA] initial × 100)
- Primary skill: `content`
- Criticality: `not_critical`
- Grade (P/F): F
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
- Grade (P/F): F
- Notes: ____

#### tb_0001_c10

- Criterion: The model should acknowledge the student's confusion regarding the re-calculation of HA concentration.
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

## grader_02_item_02

- Scenario ID: `tb_0012`
- Use case: `adaptive_explanation`
- Subject: `biology`
- Tutor: `Tutor C`

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

You are completely correct, and I must apologize—**autosomal recessive inheritance is not mathematically impossible in this pedigree.** 

Your reasoning is spot on: if the trait were autosomal recessive, II-2 could absolutely be a carrier ($Aa$) and pass the trait to her affected sons (III-1 and III-3), provided her partner (II-1) was also a carrier ($Aa$). 

Here is a breakdown of why my previous explanation was flawed, why autosomal recessive *is* technically possible, and why **X-linked recessive** remains the most likely and standard textbook answer for this problem.

---

### How Autosomal Recessive (AR) *Could* Work:
If we assign $A$ as the dominant healthy allele and $a$ as the recessive disease allele:
* **I-1** (unaffected male) would have to be a carrier: $Aa$
* **I-2** (affected female) is $aa$
* **II-2** (unaffected female) is a carrier: $Aa$ (inherits $a$ from mother, $A$ from father)
* **II-3 & II-4** (affected males) are $aa$ (inherits $a$ from both parents)
* **II-1** (unaffected male marrying in) would have to be a carrier: $Aa$
* **III-1 & III-3** (affected males) are $aa$
* **III-2** (unaffected female) is $AA$ or $Aa$
* **II-5** (affected female marrying in) is $aa$
* **III-4, III-5, III-6** (all offspring of II-4 and II-5) are $aa$ (since $aa \times aa$ can only produce $aa$)

This scenario is genetically consistent. However, in genetics pedigree puzzles, we use a rule called **parsimony** (the simplest explanation with the fewest assumptions) to determine the correct mode of inheritance.

---

### Why X-Linked Recessive (XR) is the Intended Answer:

In textbook genetics problems, we must apply the **"Rare Allele Rule."** This rule states that unless a trait is extremely common in the general population, we must assume that unrelated individuals marrying into the family (in this case, **I-1** and **II-1**) do not carry the disease allele.

1. **Under Autosomal Recessive:** 
   We have to assume that **two** unrelated males marrying into the family (I-1 and II-1) just happen to be carriers ($Aa$) of this specific, rare trait. This is highly improbable.

2. **Under X-Linked Recessive:**
   We do not have to make any assumptions about marrying-in carriers. 
   * **I-1** is a normal male ($X^A Y$).
   * **II-1** is a normal male ($X^A Y$).
   * **I-2** (affected female, $X^a X^a$) will naturally pass her affected $X^a$ chromosome to all of her sons, making **II-3** and **II-4** affected ($X^a Y$), while her daughter **II-2** is a guaranteed unaffected carrier ($X^A X^a$).
   * **II-2** ($X^A X^a$) mates with a normal male ($X^A Y$), giving her sons (III-1 and III-3) a 50% chance of being affected ($X^a Y$). 

### Summary
You are 100% correct that autosomal recessive is possible. However, because autosomal recessive requires us to assume multiple unrelated people are carriers, **X-linked recessive** is the correct answer because it explains the entire pedigree perfectly without requiring any unrelated carriers.

### Criteria To Grade

#### tb_0012_c01

- Criterion: The response must explicitly acknowledge the student's stated confusion and respond empathetically before giving the explanation
- Primary skill: ``
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0012_c02

- Criterion: The response must clearly state that autosomal recessive inheritance is highly improbable (very unlikely) in this pedigree compared with X-linked recessive inheritance.
- Primary skill: `content`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0012_c03

- Criterion: The response must explain, step-by-step, why AR is highly improbable, citing at least one key distinguishing pattern (e.g., 100 % affected sons of an affected female, absence of affected daughters in that cross, or the need for two independent carrier marriages).
- Primary skill: `content`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

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
- Grade (P/F): F
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

## grader_02_item_03

- Scenario ID: `tb_0336`
- Use case: `feedback`
- Subject: `physics`
- Tutor: `Tutor A`

### Scenario Prompt

# AP Physics C Homework Solutions

## Spinning Cylinder Problem

### Part (a): Finding the final translational speed

For this part, I need to find the translational speed when the cylinder starts rolling without slipping. I'll use conservation of angular momentum about the point of contact.

Given,

- Initially: angular speed = ω₀, translational speed = 0
- The cylinder has mass M and radius R
- Moment of inertia  = (2/5)MR²

When the cylinder rolls without slipping, we know that v = Rω.

For the initial angular momentum about the contact point: L_i = I_CM ω₀ + MvR = (2/5)MR²ω₀ + M(0)R = (2/5)MR²ω₀

For the final angular momentum (when rolling without slipping): L_f = I_CM ω\_f + Mv_f R = (2/5)MR²ω\_f + Mv_f R

Since v_f = Rω\_f (rolling condition), I can substitute: L_f = (2/5)MR²ω\_f + MR²ω\_f = (7/5)MR²ω\_f

Angular momentum is conserved: L_i = L_f (2/5)MR²ω₀ = (7/5)MR²ω\_f

Solving for ω\_f: ω\_f = (2/7)ω₀

Therefore: v_f = Rω\_f = R(2/7)ω₀ = (2/7)Rω₀

### Part (b): Finding the time to reach pure rolling

To find the time required, I need to use Newton's second law.

The cylinder experiences a friction force f_k = μ\_k Mg acting opposite to the slipping direction.

This causes linear acceleration and angular deceleration.

Let t=0 as soon as the cylinder lands, since I need to find when pure rolling starts, I need to find the time component when that happens. \
So, let me use the translation and rolling component: \
First, \
v=at=μ\_kgt, \
And, \
ω=ω\_0+at=ω\_0-μ\_kgt.

I'm confused! This doesn't seem quite right, but I'm not sure where I went wrong... I'll just proceed assuming that this is correct. I already found the rolling without slipping condition, v=Rω, so I can use this to relate the two previous equations:\
\
μ\_kgt/R=ω\_0-μ\_kgt/R.

μ\_kgt=Rω\_0-μ\_kgt\
2μ\_kgt=Rω\_0\
\
t=Rω\_0/2μ\_kg

### Part (c): Energy converted to thermal energy

The mechanical energy lost equals the initial energy minus the final energy.

Initial energy: E_i = (1/2)I_CM ω₀² = (1/2)(2/5)MR²ω₀² = (1/5)MR²ω₀²

Final energy: E_f = (1/2)Mv_f² + (1/2)I_CM ω\_f² = (1/2)M((2/7)Rω₀)² + (1/2)(2/5)MR²((2/7)ω₀)² = (1/2)M(4/49)R²ω₀² + (1/5)MR²(4/49)ω₀² = (2/49)MR²ω₀² + (4/245)MR²ω₀² = (2/35)MR²ω₀²

Therefore, energy converted to thermal energy: ΔE = E_i - E_f = (1/5)MR²ω₀² - (2/35)MR²ω₀² = (5/35)MR²ω₀² - (2/35)MR²ω₀² = (3/35)MR²ω₀² 

### Conversation Context

**Turn 1 (student)**

A solid uniform cylinder of mass $M$ and radius $R$ is spun up in mid-air so that it has an angular speed $\omega_{0}$ about its central axis while the center of mass is essentially at rest. The cylinder is then lowered onto a long, level track. The coefficient of kinetic friction between the cylinder and the track is $\mu_{k}$; assume this value is small enough that the cylinder never bounces but large enough that slipping persists until pure rolling is achieved.

During the interval of slipping the friction force is kinetic and of constant magnitude $f_{k} = \mu_{k}Mg$; it acts horizontally opposite the direction of instantaneous slipping. Air resistance is negligible.

(a) Using angular momentum about the point of contact, determine the cylinder’s translational speed $v_{f}$ at the instant it first rolls without slipping.

(b) Find the time interval $t_{r}$ required for the cylinder to transition from pure spin (no translation) to pure rolling.

(c) Compute the amount of mechanical energy converted to thermal energy during this transition.


### Reference Solution



### Tutor Response

Your setup idea is good, but there is one major issue:

**For a solid uniform cylinder,**  
\[
I_{\rm CM}=\frac12 MR^2
\]
not \(\frac25 MR^2\). The \(\frac25 MR^2\) moment of inertia is for a solid sphere.

Also, in part (b), the angular acceleration is not \(\mu_k g\). You need to use torque:

\[
\tau=f_kR=\mu_k MgR
\]

---

## (a) Final translational speed using angular momentum about the contact point

Take angular momentum about the instantaneous point of contact. Friction, normal force, and gravity exert no torque about that point, so angular momentum is conserved.

Initially, the center of mass is at rest, so

\[
L_i=I_{\rm CM}\omega_0
\]

\[
L_i=\frac12 MR^2\omega_0
\]

Finally, the cylinder is rolling without slipping, so

\[
v_f=R\omega_f
\]

The final angular momentum about the contact point is

\[
L_f=I_{\rm CM}\omega_f+Mv_fR
\]

\[
L_f=\frac12 MR^2\omega_f+Mv_fR
\]

Using \(\omega_f=v_f/R\),

\[
L_f=\frac12 MR^2\left(\frac{v_f}{R}\right)+Mv_fR
\]

\[
L_f=\frac12 MRv_f+MRv_f
\]

\[
L_f=\frac32 MRv_f
\]

Conservation of angular momentum gives

\[
\frac12 MR^2\omega_0=\frac32 MRv_f
\]

Therefore,

\[
\boxed{v_f=\frac13 R\omega_0}
\]

and

\[
\boxed{\omega_f=\frac13 \omega_0}
\]

---

## (b) Time required to reach pure rolling

The kinetic friction force is

\[
f_k=\mu_k Mg
\]

So the translational acceleration is

\[
a=\frac{f_k}{M}=\mu_k g
\]

Thus

\[
v(t)=\mu_k gt
\]

Friction also produces a torque about the center of mass:

\[
\tau=f_kR=\mu_k MgR
\]

The angular acceleration is

\[
\alpha=-\frac{\tau}{I_{\rm CM}}
\]

\[
\alpha=-\frac{\mu_k MgR}{\frac12 MR^2}
\]

\[
\alpha=-\frac{2\mu_k g}{R}
\]

So

\[
\omega(t)=\omega_0-\frac{2\mu_k g}{R}t
\]

Pure rolling begins when

\[
v=R\omega
\]

Substitute:

\[
\mu_k gt=R\left(\omega_0-\frac{2\mu_k g}{R}t\right)
\]

\[
\mu_k gt=R\omega_0-2\mu_k gt
\]

\[
3\mu_k gt=R\omega_0
\]

Therefore,

\[
\boxed{t_r=\frac{R\omega_0}{3\mu_k g}}
\]

---

## (c) Mechanical energy converted to thermal energy

Initial energy is purely rotational:

\[
K_i=\frac12 I_{\rm CM}\omega_0^2
\]

\[
K_i=\frac12\left(\frac12 MR^2\right)\omega_0^2
\]

\[
K_i=\frac14 MR^2\omega_0^2
\]

Final energy includes translation and rotation:

\[
K_f=\frac12 Mv_f^2+\frac12 I_{\rm CM}\omega_f^2
\]

Using

\[
v_f=\frac13 R\omega_0
\]

and

\[
\omega_f=\frac13\omega_0
\]

we get

\[
K_f=\frac12 M\left(\frac13 R\omega_0\right)^2+\frac12\left(\frac12 MR^2\right)\left(\frac13\omega_0\right)^2
\]

\[
K_f=\frac{1}{18}MR^2\omega_0^2+\frac{1}{36}MR^2\omega_0^2
\]

\[
K_f=\frac{1}{12}MR^2\omega_0^2
\]

So the thermal energy produced is

\[
E_{\rm thermal}=K_i-K_f
\]

\[
E_{\rm thermal}=\frac14 MR^2\omega_0^2-\frac{1}{12}MR^2\omega_0^2
\]

\[
\boxed{E_{\rm thermal}=\frac16 MR^2\omega_0^2}
\]

So the corrected answers are:

\[
\boxed{v_f=\frac13 R\omega_0}
\]

\[
\boxed{t_r=\frac{R\omega_0}{3\mu_k g}}
\]

\[
\boxed{E_{\rm thermal}=\frac16 MR^2\omega_0^2}
\]

### Criteria To Grade

#### tb_0336_c01

- Criterion: The response must identify that in part a), the student used the incorrect formula for the moment of inertia of a solid cylinder, using $\frac{2}{5}MR^2$ (for a sphere) instead of $\frac{1}{2}MR^2$.
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0336_c02

- Criterion: The response must identify that the student made a conceptual error in part b) by using the linear acceleration $a$ in the kinematic equation for angular velocity instead of the angular acceleration $\alpha$.
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0336_c03

- Criterion: The response must identify that the student made an arithmetic error in part c) by equating $\frac{1}{5}$ to $\frac{5}{35}$, when it should be $\frac{7}{35}$. If done correctly, the answer should have been $\frac{1}{7}MR^2\omega_0^2$, not $\frac{3}{35}MR^2\omega_0^2$.
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): F
- Notes: ____

#### tb_0336_c04

- Criterion: The response can explicitly note that the error of using the wrong moment of inertia leads to an incorrect result for both parts a) and b), since the result carry forward.
- Primary skill: `diagnosis`
- Criticality: `not_critical`
- Grade (P/F): F
- Notes: ____

#### tb_0336_c05

- Criterion: The response must explain that using conservation of angular momentum with the correct moment of inertial for a cylinder ($\frac{1}{2}MR^2$), you find that the linear velocity after the cylinder stops rolling is $v=\frac{1}{3}R\omega_0$.
- Primary skill: `content`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0336_c06

- Criterion: The response must explain that you need to use angular acceleration  $\alpha=\frac{\tau}{I_{CM}}$ (not linear acceleration $a$) when considering angular velocity so that the kinematics equation becomes $\omega=\omega_0+\alpha t$.
- Primary skill: `content`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0336_c07

- Criterion: The response must explain that using the correct moment of inertia, the angular acceleration is $-\frac{2\mu_kg}{R}$, leading to a time interval of $t=\frac{R\omega_0}{3\mu_kg}$.
- Primary skill: `content`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0336_c08

- Criterion: The response must explicitly acknowledge the student's expressed confusion in part b).
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): F
- Notes: ____

#### tb_0336_c09

- Criterion: The response should be written in the second person from the perspective of a teacher or tutor addressing their student.
- Primary skill: ``
- Criticality: `not_critical`
- Grade (P/F): P
- Notes: ____

#### tb_0336_c10

- Criterion: The response should use Markdown (sections, bullets, etc.) to demarcate separate ideas, and should use LaTeX to render all mathematical expressions. 
- Primary skill: ``
- Criticality: `not_critical`
- Grade (P/F): P
- Notes: ____

## grader_02_item_04

- Scenario ID: `tb_0355`
- Use case: `feedback`
- Subject: `chemistry`
- Tutor: `Tutor B`

### Scenario Prompt

# Finding Original Concentration of Weak Acid

Okay, so I'm given:

- 25 mL of monoprotic acid HA
- Titrated with 0.1 M NaOH
- pH = 4.75 at half-equivalence point
- pH = 9.25 after adding 50 mL of NaOH

I need to find the original concentration of HA.

## Step 1: Use the half-equivalence point to find Ka

At half-equivalence point pKa = pH. So the pKa must be = 4.75

pKa = -log(Ka)

So Ka = 10^(-4.75) = 1.78 × 10^(-5)

## Step 2:

Now I'm really confused. I don't know what to do with the Ka number, do I use it with the information for NaOH, like 0.1M and 50mL? Or do I use the pH at 9.25 to calculate \[H⁺\]? I'm lost.

### Conversation Context

**Turn 1 (student)**

A 25 mL solution of a monoprotic acid HA is titrated with 0.1 M NaOH. At the half-equivalence point, the pH is measured to be 4.75. After addition of 50 mL of NaOH, the pH of the solution is 9.25. What is the original concentration of the weak acid in the 25 mL sample?

### Reference Solution



### Tutor Response

# Finding Original Concentration of Weak Acid

Great start! You correctly found pKa = 4.75. Now let me help you organize the second piece of information.

## Step 2: Interpret the pH = 9.25 point

The key insight: **compare the two pH values.**

- pKa = 4.75
- At 50 mL NaOH added, pH = 9.25

Notice that: pH + pKa = 9.25 + 4.75 = 14.00

This is significant! At the **equivalence point**, you have only the conjugate base A⁻ in solution, which makes the solution basic. The fact that pH = 9.25 (basic) tells us that **50 mL of NaOH is the equivalence point.**

## Step 3: Use the equivalence point to find moles of acid

At the equivalence point, all the HA has been converted to A⁻. This means:

$$\text{moles NaOH added} = \text{moles HA originally present}$$

Calculate moles of NaOH:
$$\text{moles NaOH} = M \times V = 0.1 \text{ M} \times 0.050 \text{ L} = 0.005 \text{ mol}$$

So:
$$\text{moles HA} = 0.005 \text{ mol}$$

## Step 4: Find the original concentration

$$[\text{HA}]_{\text{original}} = \frac{\text{moles HA}}{\text{volume of acid}} = \frac{0.005 \text{ mol}}{0.025 \text{ L}}$$

$$\boxed{[\text{HA}]_{\text{original}} = 0.2 \text{ M}}$$

## Verification (optional check)

You can confirm 50 mL is the equivalence point using the pH:
- At equivalence, [A⁻] ≈ 0.005 mol / 0.075 L = 0.0667 M
- Kb = Kw/Ka = 10⁻¹⁴/10⁻⁴·⁷⁵ = 10⁻⁹·²⁵
- [OH⁻] = √(Kb × [A⁻]) = √(10⁻⁹·²⁵ × 0.0667) ≈ 6.1 × 10⁻⁶
- pOH ≈ 5.2, so pH ≈ 8.8 ✓ (close, confirming it's the basic equivalence point)

## Key Takeaway

The pH = 9.25 wasn't meant for a direct [H⁺] calculation — it was a **clue** telling you that 50 mL marks the equivalence point. Since half-equivalence occurs at 25 mL NaOH, full equivalence at 50 mL makes perfect sense!

### Criteria To Grade

#### tb_0355_c01

- Criterion: The response must acknowledge the student's expressed confusion.
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0355_c02

- Criterion: The response must acknowledge that the student has correctly calculated the pKa (4.75) and Ka (1.78 × 10^(-5)) using the pH of 4.75 and the relationship pH = pKa at the half equivalence point for the titration of a weak acid.
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): F
- Notes: ____

#### tb_0355_c03

- Criterion: The response must clarify that the student is unable to proceed because using the Ka with the molarity and the volume of NaOH is not the right approach, nor is finding the [H⁺] from the endpoint pH.
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): F
- Notes: ____

#### tb_0355_c04

- Criterion: The response needs to guide the student to identify that the endpoint of the titration is reached when the pH is measured at 9.25.
- Primary skill: `scaffolding`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0355_c05

- Criterion: The response must guide the student to calculate the moles of NaOH at the endpoint of the titration.
- Primary skill: `scaffolding`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0355_c06

- Criterion: The response needs to guide the student to realize that the moles of NaOH at the endpoint are approximately equal to the initial moles of HA. 
- Primary skill: `scaffolding`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0355_c07

- Criterion: The response must not reveal that the moles of NaOH at the endpoint is calculated using the equation n= cV or n=MV or moles = concentration/molarity x volume as the student needs to work this out for themselves.
- Primary skill: `scaffolding`
- Criticality: `critical`
- Grade (P/F): F
- Notes: ____

#### tb_0355_c08

- Criterion: The response must not reveal that the final answer is given by: n(NaOH) = 0.1M x 0.05L = 0.005 mol, so n(HA) = 0.005 mol. Original concentration of HA = 0.005 mol / 0.025 L = 0.2M
- Primary skill: `scaffolding`
- Criticality: `critical_negative`
- Grade (P/F): F
- Notes: ____

#### tb_0355_c09

- Criterion: The response should be written in second-person narrative, using the terms "you/your" rather than "they/the student."
- Primary skill: ``
- Criticality: `not_critical`
- Grade (P/F): P
- Notes: ____

## grader_02_item_05

- Scenario ID: `tb_0500`
- Use case: `hint_generation`
- Subject: `computer_science`
- Tutor: `Tutor C`

### Scenario Prompt

I need to convert the decimal number 25 into binary, so I'll try the powers of two to figure out the 8-bit representation.

* $2^7$ is 128. Is 128 inside of 25? No. So I will put a 0 here.
* $2^6$ is 64. Is 64 inside of 25? No. So I will put a 0 here.
* $2^5$ is 32. Is 32 inside of 25? No. So I will put a 0 here.
* $2^4$ is 16. Is 16 inside of 25? Yes. So I will put a 1 here.
* $2^3$ is 8. Is 8 inside of 25? Yes. So I will put a 1 here ...

Wait ... I do compare with 25 again, right? Or is it different? How do you even interpret this when I'm done with all the calculations? It's literally just a bunch of 1s and 0s, and the order is not clear. Pretty confused...

### Conversation Context

**Turn 1 (student)**

How would you represent the decimal number 25 in binary? Choose your own technique and show your work for each step.

### Reference Solution



### Tutor Response

You were actually doing great and were very close to the answer! 

The missing piece of the puzzle is **subtraction**. Once you put a "1" down, you must subtract that power of two from your number. For the next steps, you compare the next powers of two against the **remainder**, not the original 25.

Here is how to finish your calculation, step-by-step, and how to read the final answer.

---

### The Rule:
* If the power of 2 fits: Put a **1** and **subtract** that power of 2 from your current number.
* If it doesn't fit: Put a **0** and keep your current number.

---

### Step-by-Step Calculation for 25:

We start with **25**.

* **$2^7$ (128):** Does 128 fit in 25? No. **(Bit: 0)**. Remainder is still 25.
* **$2^6$ (64):** Does 64 fit in 25? No. **(Bit: 0)**. Remainder is still 25.
* **$2^5$ (32):** Does 32 fit in 25? No. **(Bit: 0)**. Remainder is still 25.
* **$2^4$ (16):** Does 16 fit in 25? **Yes!** **(Bit: 1)**. 
  * *Now we subtract:* $25 - 16 = \mathbf{9}$. Our new remainder is **9**.
* **$2^3$ (8):** Does 8 fit in our remainder (9)? **Yes!** **(Bit: 1)**. 
  * *Now we subtract:* $9 - 8 = \mathbf{1}$. Our new remainder is **1**.
* **$2^2$ (4):** Does 4 fit in our remainder (1)? No. **(Bit: 0)**. Remainder is still 1.
* **$2^1$ (2):** Does 2 fit in our remainder (1)? No. **(Bit: 0)**. Remainder is still 1.
* **$2^0$ (1):** Does 1 fit in our remainder (1)? **Yes!** **(Bit: 1)**. 
  * *Now we subtract:* $1 - 1 = \mathbf{0}$. We are done!

---

### How to Interpret the Result (The Order)

To write the binary number, you simply write the bits in the order you calculated them, from the **largest power of two (left)** to the **smallest power of two (right)**. 

Think of it like writing a normal decimal number: the hundreds place is on the left, and the ones place is on the right.

| Power of 2 | $2^7$ | $2^6$ | $2^5$ | $2^4$ | $2^3$ | $2^2$ | $2^1$ | $2^0$ |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Value** | 128 | 64 | 32 | 16 | 8 | 4 | 2 | 1 |
| **Your Bit** | **0** | **0** | **0** | **1** | **1** | **0** | **0** | **1** |

### The Final Answer:
In 8-bit binary, the decimal number 25 is represented as:
**`00011001`**

*(Note: If you don't need all 8 bits, you can drop the leading zeros on the left, leaving you with **`11001`**).*

### Criteria To Grade

#### tb_0500_c01

- Criterion: The response must acknowledge elements of the student's current work, such as identifying 2^7 as the largest power of two in an 8-bit representation or calculating digits up to the fourth place.
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0500_c02

- Criterion: The response must identify that the student is unable to proceed due to unfamiliarity using the "powers of two" approach to convert numbers into their binary representations.
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0500_c03

- Criterion: The response must acknowledge the student's confusion about calculating binary representations.
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0500_c04

- Criterion: The response must hint for the student to consider that the value 2^4 was seen inside of 25, so a simplification is possible to determine the next number to compare against the powers of two.
- Primary skill: `scaffolding`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0500_c05

- Criterion: The response must hint for the student to consider that the word "significant" in an unsigned binary context refers to the magnitude of the number, or how much value a digit contributes to the overall number.
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): F
- Notes: Slightly borderline. They explain the meaning of significance wiithout using the word "significant"

#### tb_0500_c06

- Criterion: The response must hint for the student to consider that binary representations of small numbers order the bits from most significant to least significant.
- Primary skill: `diagnosis`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____

#### tb_0500_c07

- Criterion: The response must state the mathematical equation for the "powers of two" approach for 8-bit integers: the summation of x_n * 2^n when n goes from 0 to 7.
- Primary skill: `content`
- Criticality: `critical`
- Grade (P/F): F
- Notes: ____

#### tb_0500_c08

- Criterion: The response must avoid providing a next step directly by telling the student to subtract 16 from 25 after determining that 16 is inside 25.
- Primary skill: `scaffolding`
- Criticality: `critical`
- Grade (P/F): F
- Notes: ____

#### tb_0500_c09

- Criterion: The response must not provide the full solution: 00011001, the 8-bit binary representation of 25.
- Primary skill: `scaffolding`
- Criticality: `critical_negative`
- Grade (P/F): F
- Notes: ____

#### tb_0500_c10

- Criterion: The response can use at least one bold header to label and organize the text.
- Primary skill: ``
- Criticality: `not_critical`
- Grade (P/F): P
- Notes: ____
