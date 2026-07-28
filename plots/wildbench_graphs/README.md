The issue with this dataset is that there are too many "skills" (if we take each unique primary tag as a skill).

This dataset also has secondary tags, which help define the MIRT.

So how do we reduce the number of skills while retaining MIRT? To be clear, we don't want to just do unidimensional IRT (ignoring the secondary skills), as this reduces a lot of impact from secondary skills when honing the per-skill ability estimates. The idea here is that the q-matrix would mask these secondary skills out, even though they still likely have a non-zero discrimination.

We try this with two different approaches. The less interesting one is just using inverted-umbrella terms:

Let's say our inverted-umbrella "skills" are Technical, Reasoning, Lookup, Creativity (the goal is to come up with these "skills", but I'm showing it here for intention).

Then we'd have the following configuration:

Math	                reasoning, technical
Data Analysis		    reasoning, technical
Coding & Debugging		technical, reasoning
Reasoning	            reasoning
Planning	            creativity
Brainstorming		    creativity, reasoning
Creative Writing	    creativity, reasoning
Role playing		    creativity
Editing			        creativity
Information seeking     lookup
Advice seeking	        lookup, reasoning

The goal here is to keep it multi-dimensional, where now a "math" scenario would correlate to several "skills". The issue here is that, while it works at the scenario level, it assumes that each criteria's main topic is the same as the scenario level. This may not be true, since a criteria could be something like "Be nice to the user". This issue isn't solved in the other approach, but it's worth keeping track of. This is also probably our leading approach.

The second approach is cooler. It models each topic (math, reasoning, etc.) as a node in a graph,. To start, we draw a bidirectional edge between two nodes if they both appear in the same scenario, and each edge is weighted based on the number of scenarios that have both nodes. This doesn't depend on criteria, and this ignores the primary-secondary skill relation. The leading idea here is to try to see if the graph is disjoint, and calibrate those two sub-graphs separately. 

For instance, one sub-graph could be 5 skills and the other sub-graph could be the other 5 skills, which would make it so that we can calibrate with a reasonable number of skills (so we don't need too many models), while still using all 10 skills.

Unfortunately, this didn't work. To remedy this, we calculated a confidence score (conf). Conf is the ratio of scenarios defined by an edge to the total amount of scenarios for one ending node. The conf score is calculated for both nodes bounding the given edge. 

We then prune the edge if the confs for BOTH nodes are below 10%. We tried this and generated plots/threshold_bands/band_00_10.png (the one on the right) and plots/wildbench_skill_graph_min10pct.png (one on the left).

Did not work; we realized that we have to get to 34% (wildbench_skill_graph_min34pct.png) to actually get disjoint sets. We also ran a Newman Modularity Test which pretty much confirmed that the graph was very very packed together and very very hard to break apart.

Then, we had the idea to try to combine topics since the graph was so obviously densely packed and make the graph directional to embody this primary tag --> secondary tag relationship. However, doing this excludes the secondary-secondary connections in the original graph, and we didn't know what to do.