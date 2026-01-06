// Books in Books - Modern 2D Graph Visualizer with D3.js

let svg, simulation, nodes = [], links = [];
let allBooks = [], allReferences = [];
let startingBooks = [];
let selectedNode = null;
let hoveredNode = null;
let linkGroup, nodeGroup;
let zoom;

// Tooltip functions
function initTooltip() {
  // Tooltip is handled by D3 selection
}

function showTooltip(event, d) {
  const tooltip = d3.select("#node-tooltip");
  tooltip
    .style("left", (event.pageX + 10) + "px")
    .style("top", (event.pageY - 10) + "px")
    .style("display", "block")
    .html(`
      <div class="tooltip-title">${d.title}</div>
      <div class="tooltip-author">${d.author || 'Unknown'}</div>
    `);
}

function hideTooltip() {
  d3.select("#node-tooltip").style("display", "none");
}

function init2D() {
  const container = d3.select("#graph-container");
  if (container.empty()) {
    console.error('Graph container not found!');
    return;
  }
  
  // Clear existing content but keep graph-controls
  container.select("svg").remove();
  
  // Get dimensions - ensure we have valid dimensions
  let width = container.node().clientWidth;
  let height = container.node().clientHeight;
  
  if (width === 0 || height === 0) {
    // Fallback dimensions
    width = window.innerWidth - 100;
    height = window.innerHeight - 300;
    console.warn('Container has zero dimensions, using fallback:', width, height);
  }
  
  console.log('Initializing 2D with dimensions:', width, 'x', height);
  
  // Create SVG with Processing 2010 style background
  svg = container.append("svg")
    .attr("width", width)
    .attr("height", height)
    .style("background", "#0a0a0a") // Dark Processing background
    .style("display", "block");
  
  console.log('SVG created:', width, 'x', height);
  
  // Add defs for Processing 2010 style - minimal, geometric
  const defs = svg.append("defs");
  
  // Simple shadow filter for depth (Processing style)
  const shadowFilter = defs.append("filter")
    .attr("id", "processing-shadow")
    .attr("x", "-50%")
    .attr("y", "-50%")
    .attr("width", "200%")
    .attr("height", "200%");
  shadowFilter.append("feGaussianBlur")
    .attr("in", "SourceAlpha")
    .attr("stdDeviation", "2")
    .attr("result", "blur");
  shadowFilter.append("feOffset")
    .attr("in", "blur")
    .attr("dx", "1")
    .attr("dy", "1")
    .attr("result", "offsetBlur");
  const feMerge = shadowFilter.append("feMerge");
  feMerge.append("feMergeNode").attr("in", "offsetBlur");
  feMerge.append("feMergeNode").attr("in", "SourceGraphic");
  
  // Create groups for links and nodes
  linkGroup = svg.append("g").attr("class", "links");
  nodeGroup = svg.append("g").attr("class", "nodes");
  
  // Setup zoom
  zoom = d3.zoom()
    .scaleExtent([0.1, 4])
    .on("zoom", (event) => {
      nodeGroup.attr("transform", event.transform);
      linkGroup.attr("transform", event.transform);
    });
  
  svg.call(zoom);
  
  console.log('2D initialization complete');
}

function renderGraph(books, references) {
  console.log('renderGraph called with', books.length, 'books and', references.length, 'references');
  
  // Check if D3 is loaded
  if (typeof d3 === 'undefined') {
    console.error('D3.js is not loaded!');
    return;
  }
  
  if (!svg || svg.empty()) {
    console.log('Initializing 2D visualization...');
    init2D();
    if (!svg || svg.empty()) {
      console.error('Failed to initialize SVG!');
      return;
    }
  }
  
  allBooks = books;
  allReferences = references;
  
  // Filter out books without authors (they shouldn't be nodes)
  books = books.filter(book => {
    const hasAuthor = book.author && book.author.trim() && book.author.toLowerCase() !== 'unknown';
    if (!hasAuthor) {
      console.log(`Filtering out book without author: "${book.title}" (ID: ${book.id})`);
    }
    return hasAuthor;
  });
  
  startingBooks = books.filter(b => b.is_starting).map(b => String(b.id));
  
  console.log(`Starting books found: ${startingBooks.length}`, startingBooks);
  console.log(`Books with authors: ${books.length}`);
  
  // Remove duplicate books - prioritize starting books, then by ID (lower ID = created first)
  const booksByTitle = new Map();
  books.forEach(book => {
    const titleKey = book.title.toLowerCase().trim();
    const existing = booksByTitle.get(titleKey);
    if (!existing) {
      booksByTitle.set(titleKey, book);
    } else {
      // Prefer starting books, then lower ID
      const existingIsStarting = startingBooks.includes(String(existing.id));
      const currentIsStarting = startingBooks.includes(String(book.id));
      if (currentIsStarting && !existingIsStarting) {
        booksByTitle.set(titleKey, book);
      } else if (currentIsStarting === existingIsStarting && book.id < existing.id) {
        booksByTitle.set(titleKey, book);
      }
    }
  });
  
  const uniqueBooks = Array.from(booksByTitle.values());
  const uniqueBookIds = new Set(uniqueBooks.map(b => String(b.id)));
  
  // Create ID remapping for duplicates
  const idRemap = new Map();
  books.forEach(book => {
    const titleKey = book.title.toLowerCase().trim();
    const uniqueBook = booksByTitle.get(titleKey);
    if (uniqueBook && String(book.id) !== String(uniqueBook.id)) {
      idRemap.set(String(book.id), String(uniqueBook.id));
    }
  });
  
  // Remap references and filter out references to books without authors
  const uniqueReferences = references
    .map(ref => ({
      source: idRemap.get(String(ref.source)) || String(ref.source),
      target: idRemap.get(String(ref.target)) || String(ref.target)
    }))
    .filter(ref =>
      uniqueBookIds.has(ref.source) &&
      uniqueBookIds.has(ref.target) &&
      ref.source !== ref.target // Remove self-references
    );
  
  console.log(`Removed duplicates: ${books.length} -> ${uniqueBooks.length} books`);
  console.log(`Remapped ${idRemap.size} duplicate IDs`);
  books = uniqueBooks;
  references = uniqueReferences;
  
  // Update startingBooks to use remapped IDs
  startingBooks = startingBooks
    .map(id => idRemap.get(id) || id)
    .filter(id => uniqueBookIds.has(id));
  
  console.log(`Final starting books: ${startingBooks.length}`, startingBooks);
  
  // Filter to only show nodes DIRECTLY referenced by starting books (no sub-networks from normal nodes)
  const connectedBookIds = new Set(startingBooks);
  
  // Only add nodes that are DIRECTLY referenced by starting books (one level deep)
  // This prevents normal nodes from creating their own sub-networks
  references.forEach(ref => {
    const sourceId = String(ref.source);
    const targetId = String(ref.target);
    
    // Only add target if source is a starting book
    if (connectedBookIds.has(sourceId) && !connectedBookIds.has(targetId)) {
      // Check if source is actually a starting book (not just a normal node we added)
      if (startingBooks.includes(sourceId)) {
        connectedBookIds.add(targetId);
      }
    }
  });
  
  console.log(`Found ${connectedBookIds.size - startingBooks.length} nodes directly referenced by starting books`);
  
  // Filter books and references to only include connected ones
  const connectedBooks = books.filter(b => connectedBookIds.has(String(b.id)));
  const connectedReferences = references.filter(ref => 
    connectedBookIds.has(String(ref.source)) && connectedBookIds.has(String(ref.target))
  );
  
  console.log(`Filtered: ${connectedBooks.length} connected books (from ${books.length} total)`);
  console.log(`Filtered: ${connectedReferences.length} connected references (from ${references.length} total)`);
  console.log(`Starting books: ${startingBooks.length}`);
  
  // Get container dimensions
  const container = d3.select("#graph-container");
  const width = container.node().clientWidth || window.innerWidth - 100;
  const height = container.node().clientHeight || window.innerHeight - 300;
  
  // Clear previous
  linkGroup.selectAll("*").remove();
  nodeGroup.selectAll("*").remove();
  
  // Create nodes data (using filtered connected books)
  nodes = connectedBooks.map((book, i) => {
    const isStarting = startingBooks.includes(String(book.id));
    
    // Count unique connections (not just total references)
    const connectedBookIds = new Set();
    connectedReferences.forEach(ref => {
      if (String(ref.source) === String(book.id)) {
        connectedBookIds.add(String(ref.target));
      }
      if (String(ref.target) === String(book.id)) {
        connectedBookIds.add(String(ref.source));
      }
    });
    const connections = connectedBookIds.size;
    
    // Processing 2010 style - simple, geometric, minimal
    let size, color, strokeColor;
    if (isStarting) {
      size = Math.max(18, Math.min(32, 18 + connections * 2));
      color = "#FFD700"; // Bright yellow/gold - Processing accent color
      strokeColor = "#FFA500"; // Orange stroke for depth
    } else if (connections > 0) {
      size = Math.max(8, Math.min(18, 8 + connections * 1.5));
      // Simple blue - Processing style
      color = "#4A90E2"; // Clean blue
      strokeColor = "#2E5C8A"; // Darker blue stroke
    } else {
      size = 6;
      color = "#666666";
      strokeColor = "#444444";
    }
    
    // Better initial positioning - starting books closer together
    let x, y;
    if (isStarting) {
      const startingIndex = startingBooks.indexOf(String(book.id));
      const angle = (startingIndex / Math.max(startingBooks.length, 1)) * 2 * Math.PI;
      const radius = 80; // Closer together
      x = width / 2 + Math.cos(angle) * radius;
      y = height / 2 + Math.sin(angle) * radius;
    } else {
      const angle = (i / connectedBooks.length) * 2 * Math.PI;
      const radius = 100 + Math.random() * 100;
      x = width / 2 + Math.cos(angle) * radius;
      y = height / 2 + Math.sin(angle) * radius;
    }
    
    return {
      id: String(book.id),
      title: book.title,
      author: book.author,
      isStarting: isStarting,
      connections: connections,
      size: size,
      color: color,
      strokeColor: strokeColor,
      x: x,
      y: y,
      vx: 0,
      vy: 0
    };
  });
  
  console.log('Created', nodes.length, 'nodes');
  
  // Create links data
  links = connectedReferences.map(ref => ({
    source: String(ref.source),
    target: String(ref.target),
    highlighted: false
  }));
  
  // Filter out self-references
  links = links.filter(link => {
    const sourceId = typeof link.source === 'object' ? link.source.id : link.source;
    const targetId = typeof link.target === 'object' ? link.target.id : link.target;
    return sourceId !== targetId;
  });
  
  console.log(`Filtered out self-references, ${links.length} links remaining`);
  
  // Create force simulation
  console.log('Creating force simulation...');
  simulation = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(links)
      .id(d => d.id)
      .distance(d => {
        // Longer distance for non-starting connections
        const sourceNode = typeof d.source === 'object' ? d.source : nodes.find(n => n.id === d.source);
        const targetNode = typeof d.target === 'object' ? d.target : nodes.find(n => n.id === d.target);
        const sourceIsStarting = sourceNode?.isStarting;
        const targetIsStarting = targetNode?.isStarting;
        // If connecting two starting books, keep them closer
        if (sourceIsStarting && targetIsStarting) {
          return 80;
        }
        return (sourceIsStarting || targetIsStarting) ? 100 : 150;
      })
      .strength(0.5)
    )
    .force("charge", d3.forceManyBody()
      .strength(d => d.isStarting ? -500 : -400) // Reduced repulsion for starting books
    )
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("collision", d3.forceCollide()
      .radius(d => d.size + 5)
      .strength(0.8)
    )
    // Add a force to keep starting books closer together
    .force("startingCluster", function(alpha) {
      const startingNodes = nodes.filter(n => n.isStarting);
      if (startingNodes.length <= 1) return;
      
      const centerX = d3.mean(startingNodes, d => d.x) || width / 2;
      const centerY = d3.mean(startingNodes, d => d.y) || height / 2;
      const clusterRadius = 150; // Keep starting books within this radius
      
      startingNodes.forEach(node => {
        const dx = node.x - centerX;
        const dy = node.y - centerY;
        const distance = Math.sqrt(dx * dx + dy * dy);
        
        if (distance > clusterRadius) {
          const force = (distance - clusterRadius) * 0.1;
          node.vx -= (dx / distance) * force * alpha;
          node.vy -= (dy / distance) * force * alpha;
        }
      });
    })
    .alpha(1) // Start with high energy
    .alphaDecay(0.0228) // Slow decay
    .on("end", () => console.log('Simulation ended'));
  
  console.log('Force simulation created');
  
  // Draw links - WCAG friendly: higher contrast, thicker lines
  console.log('Drawing', links.length, 'links');
  const link = linkGroup.selectAll("line")
    .data(links)
    .enter().append("line")
    .attr("stroke", "#888888") // Lighter gray for better contrast
    .attr("stroke-opacity", 0.6) // Higher opacity for visibility
    .attr("stroke-width", 2.5) // Thicker for better visibility
    .style("stroke-linecap", "round");
  
  console.log('Links drawn:', link.size());
  
  // Draw nodes - Processing 2010 style: simple circles with stroke
  console.log('Drawing', nodes.length, 'nodes');
  const node = nodeGroup.selectAll("circle")
    .data(nodes)
    .enter().append("circle")
    .attr("r", 0) // Start at 0 for animation
    .attr("fill", d => d.color)
    .attr("stroke", d => d.strokeColor)
    .attr("stroke-width", 2)
    .style("cursor", "pointer")
    .style("filter", "url(#processing-shadow)")
    .style("opacity", 0) // Start invisible for animation
    .on("mouseover", function(event, d) {
      showTooltip(event, d);
      d3.select(this)
        .transition()
        .duration(150)
        .attr("r", d.size * 1.15)
        .attr("stroke-width", 3);
    })
    .on("mouseout", function(event, d) {
      hideTooltip();
      d3.select(this)
        .transition()
        .duration(150)
        .attr("r", d.size)
        .attr("stroke-width", 2);
    })
    .on("click", function(event, d) {
      event.stopPropagation();
      selectNode(d);
    })
    .call(drag(simulation)); // Enable dragging for all nodes (drag function checks if starting)
  
  // Initial animation will be triggered in simulation tick when nodes are positioned
  
  // Track if initial animation has run
  let initialAnimationRun = false;
  
  // Update positions on simulation tick
  simulation.on("tick", () => {
    link
      .attr("x1", d => {
        const source = typeof d.source === 'object' ? d.source : nodes.find(n => n.id === d.source);
        return source?.x || width / 2;
      })
      .attr("y1", d => {
        const source = typeof d.source === 'object' ? d.source : nodes.find(n => n.id === d.source);
        return source?.y || height / 2;
      })
      .attr("x2", d => {
        const target = typeof d.target === 'object' ? d.target : nodes.find(n => n.id === d.target);
        return target?.x || width / 2;
      })
      .attr("y2", d => {
        const target = typeof d.target === 'object' ? d.target : nodes.find(n => n.id === d.target);
        return target?.y || height / 2;
      });
    
    node
      .attr("cx", d => d.x || width / 2)
      .attr("cy", d => d.y || height / 2);
    
    // Trigger initial animation once nodes are positioned
    if (!initialAnimationRun && nodes.length > 0 && nodes[0].x !== undefined && nodes[0].x !== null) {
      initialAnimationRun = true;
      // Small delay to ensure positions are set
      setTimeout(() => {
        node.transition()
          .duration(400)
          .delay((d, i) => Math.min(i * 8, 200))
          .ease(d3.easeBackOut.overshoot(1.5))
          .attr("r", d => d.size)
          .style("opacity", 1);
      }, 50);
    }
    
    // Update node counts periodically (not every tick to avoid performance issues)
    if (Math.random() < 0.01) { // Update roughly 1% of ticks
      updateNodeCounts();
    }
  });
  
  console.log('Simulation started');
  
  // Click on background to deselect
  svg.on("click", function(event) {
    if (event.target === this) {
      deselectNode();
    }
  });
  
  // Render starting books list (use allBooks to get full info)
  // Make sure allBooks is populated before rendering
  if (allBooks.length === 0) {
    allBooks = books; // Fallback if allBooks wasn't set
  }
  renderStartingBooksList(books);
  
  // Update visibility based on hidden nodes
  updateNodeVisibility();
  
  // Update node counts display
  updateNodeCounts();
  
  initTooltip();
}

function updateNodeCounts() {
  if (!nodes || nodes.length === 0) {
    console.log('No nodes available for counting');
    return;
  }
  
  const visibleNodes = nodes.filter(n => !hiddenNodeIds.has(n.id));
  const goldenNodes = visibleNodes.filter(n => n.isStarting);
  const normalNodes = visibleNodes.filter(n => !n.isStarting);
  
  console.log('Updating node counts - Golden:', goldenNodes.length, 'Normal:', normalNodes.length);
  
  let statsDiv = d3.select("#node-counts");
  if (statsDiv.empty()) {
    console.log('Creating node-counts div');
    statsDiv = d3.select("#graph-container").append("div")
      .attr("id", "node-counts")
      .attr("class", "node-counts");
  }
  
  statsDiv.html(`
    <div class="count-line">Golden: ${goldenNodes.length}</div>
    <div class="count-line">Nodes: ${normalNodes.length}</div>
  `);
  
  console.log('Node counts updated');
}

// Stats function removed - no longer displaying stats panel

function drag(simulation) {
  return d3.drag()
    .on("start", function(event, d) {
      // Only allow dragging starting books
      if (!d.isStarting) {
        event.sourceEvent.stopPropagation();
        return;
      }
      if (!event.active) simulation.alphaTarget(0.3).restart();
      d.fx = d.x;
      d.fy = d.y;
    })
    .on("drag", function(event, d) {
      // Only allow dragging starting books
      if (!d.isStarting) return;
      // Account for zoom transform - event.x and event.y are already in transformed coordinates
      d.fx = event.x;
      d.fy = event.y;
    })
    .on("end", function(event, d) {
      if (!d.isStarting) return;
      if (!event.active) simulation.alphaTarget(0);
      // Allow node to move freely again
      d.fx = null;
      d.fy = null;
    });
}

function selectNode(node) {
  if (selectedNode && selectedNode.id === node.id) {
    deselectNode();
    return;
  }
  
  selectedNode = node;
  highlightPathToNode(node);
  console.log('Selected node:', node.title);
}

function deselectNode() {
  selectedNode = null;
  highlightPathToNode(null);
}

function highlightPathToNode(targetNode) {
  if (!targetNode) {
    // Remove all highlights
    linkGroup.selectAll("line")
      .attr("stroke", "#888888")
      .attr("stroke-width", 2);
    return;
  }
  
  // Find shortest path from any starting book to target
  let shortestPath = null;
  startingBooks.forEach(startId => {
    const path = findShortestPath(startId, targetNode.id);
    if (path && (!shortestPath || path.length < shortestPath.length)) {
      shortestPath = path;
    }
  });
  
  if (!shortestPath) {
    console.log('No path found to', targetNode.title);
    return;
  }
  
  // Create set of edges in the path
  const pathEdges = new Set();
  for (let i = 0; i < shortestPath.length - 1; i++) {
    const edgeKey = `${shortestPath[i]}-${shortestPath[i + 1]}`;
    const edgeKeyReverse = `${shortestPath[i + 1]}-${shortestPath[i]}`;
    pathEdges.add(edgeKey);
    pathEdges.add(edgeKeyReverse);
  }
  
  // Highlight path edges
  linkGroup.selectAll("line")
    .attr("stroke", d => {
      const sourceId = typeof d.source === 'object' ? d.source.id : d.source;
      const targetId = typeof d.target === 'object' ? d.target.id : d.target;
      const edgeKey = `${sourceId}-${targetId}`;
      const edgeKeyReverse = `${targetId}-${sourceId}`;
      if (pathEdges.has(edgeKey) || pathEdges.has(edgeKeyReverse)) {
        return "#ffd700";
      }
      return "#444444";
    })
    .attr("stroke-width", d => {
      const sourceId = typeof d.source === 'object' ? d.source.id : d.source;
      const targetId = typeof d.target === 'object' ? d.target.id : d.target;
      const edgeKey = `${sourceId}-${targetId}`;
      const edgeKeyReverse = `${targetId}-${sourceId}`;
      if (pathEdges.has(edgeKey) || pathEdges.has(edgeKeyReverse)) {
        return 4;
      }
      return 2;
    });
}

function findShortestPath(startId, targetId) {
  const queue = [[startId]];
  const visited = new Set([startId]);
  
  while (queue.length > 0) {
    const path = queue.shift();
    const currentId = path[path.length - 1];
    
    if (String(currentId) === String(targetId)) {
      return path;
    }
    
    links.forEach(link => {
      const sourceId = typeof link.source === 'object' ? link.source.id : link.source;
      const targetId = typeof link.target === 'object' ? link.target.id : link.target;
      let nextId = null;
      
      if (String(sourceId) === String(currentId)) {
        nextId = targetId;
      } else if (String(targetId) === String(currentId)) {
        nextId = sourceId;
      }
      
      if (nextId && !visited.has(String(nextId))) {
        visited.add(String(nextId));
        queue.push([...path, String(nextId)]);
      }
    });
  }
  
  return null;
}

let hiddenNodeIds = new Set();

// Set up event delegation ONCE on the parent container
let clickHandlerAttached = false;

function setupBookListClickHandler() {
  if (clickHandlerAttached) return;
  
  const list = document.getElementById("starting-books-list");
  if (!list) {
    console.error('Starting books list container not found!');
    return;
  }
  
  // Use event delegation - attach ONE listener to the parent
  list.addEventListener('click', function(e) {
    // Find the closest book item (works even if clicking on child elements)
    const card = e.target.closest('.starting-book-item');
    if (card) {
      e.stopPropagation();
      e.preventDefault();
      const bookId = card.getAttribute('data-book-id');
      if (bookId) {
        console.log('=== BOOK CLICKED (Event Delegation) ===');
        console.log('Book ID:', bookId);
        console.log('Element:', card);
        toggleBookVisibility(bookId);
      } else {
        console.error('No book ID found on clicked element!');
      }
      return false;
    }
  }, true); // Use capture phase to catch early
  
  clickHandlerAttached = true;
  console.log('Book list click handler attached via event delegation');
}

function renderStartingBooksList(books) {
  const list = d3.select("#starting-books-list");
  if (list.empty()) {
    console.error('Starting books list container not found!');
    return;
  }
  
  // Set up event delegation first (only once)
  setupBookListClickHandler();
  
  // Use allBooks if available, otherwise use books parameter
  const sourceBooks = allBooks.length > 0 ? allBooks : books;
  
  // Filter to get starting books with full info
  const startingBooksList = sourceBooks.filter(b => {
    const bookId = String(b.id);
    const isStarting = startingBooks.includes(bookId);
    return isStarting;
  });
  
  console.log('=== RENDERING STARTING BOOKS LIST ===');
  console.log('Starting books IDs:', startingBooks);
  console.log('Source books count:', sourceBooks.length);
  console.log('Found starting books:', startingBooksList.length);
  startingBooksList.forEach(b => {
    console.log(`  - ID ${b.id}: "${b.title || 'NO TITLE'}" by ${b.author || 'Unknown'}`);
  });
  
  if (startingBooksList.length === 0) {
    console.warn('No starting books found to display!');
    console.warn('Available book IDs:', sourceBooks.map(b => b.id));
    return;
  }
  
  // Use D3's update pattern - update existing, enter new, exit old
  const bookItems = list.selectAll("div.starting-book-item")
    .data(startingBooksList, d => String(d.id));
  
  // Remove old items
  bookItems.exit().remove();
  
  // Update existing items (update classes for hidden state)
  bookItems
    .attr("class", d => {
      const isHidden = hiddenNodeIds.has(String(d.id));
      return "starting-book-item" + (isHidden ? " hidden" : "");
    });
  
  // Add new items
  const bookItemsEnter = bookItems.enter()
    .append("div")
    .attr("class", d => {
      const isHidden = hiddenNodeIds.has(String(d.id));
      return "starting-book-item" + (isHidden ? " hidden" : "");
    })
    .attr("data-book-id", d => d.id) // Store ID in data attribute
    .style("pointer-events", "all")
    .style("cursor", "pointer")
    .style("user-select", "none");
  
  // Add title to new items
  bookItemsEnter.append("span")
    .attr("class", "book-title")
    .text(d => d.title || 'Unknown Title')
    .style("display", "block");
  
  // Add author to new items
  bookItemsEnter.append("span")
    .attr("class", "book-author")
    .text(d => `by ${d.author || 'Unknown Author'}`)
    .style("display", "block");
  
  // Add count to new items
  bookItemsEnter.append("span")
    .attr("class", "book-count")
    .text(d => {
      const referencedBookIds = new Set();
      const refsToUse = allReferences.length > 0 ? allReferences : [];
      refsToUse.forEach(ref => {
        if (String(ref.source) === String(d.id)) {
          referencedBookIds.add(String(ref.target));
        }
      });
      return `${referencedBookIds.size} books`;
    })
    .style("display", "block");
  
  // Update counts for existing items too
  bookItems.select("span.book-count")
    .text(d => {
      const referencedBookIds = new Set();
      const refsToUse = allReferences.length > 0 ? allReferences : [];
      refsToUse.forEach(ref => {
        if (String(ref.source) === String(d.id)) {
          referencedBookIds.add(String(ref.target));
        }
      });
      return `${referencedBookIds.size} books`;
    });
  
  // Merge enter and update selections for hover effects
  bookItems.merge(bookItemsEnter)
    .on("mouseenter", function() {
      d3.select(this).style("transform", "translateY(-2px)");
    })
    .on("mouseleave", function() {
      d3.select(this).style("transform", "translateY(0)");
    });
  
  console.log('Rendered', bookItems.size() + bookItemsEnter.size(), 'book items');
}

function toggleBookVisibility(bookId) {
  const bookIdStr = String(bookId);
  
  console.log('=== TOGGLING BOOK VISIBILITY ===');
  console.log('Book ID:', bookIdStr);
  console.log('Is starting book?', startingBooks.includes(bookIdStr));
  console.log('Current hidden nodes:', Array.from(hiddenNodeIds));
  
  // Toggle the hidden state
  if (hiddenNodeIds.has(bookIdStr)) {
    // Show the book and its connections
    hiddenNodeIds.delete(bookIdStr);
    console.log('✓ Showing book:', bookIdStr);
      } else {
    // Hide the book and its connections
    hiddenNodeIds.add(bookIdStr);
    console.log('✗ Hiding book:', bookIdStr);
  }
  
  console.log('Updated hidden nodes:', Array.from(hiddenNodeIds));
  
  // Update visualization immediately
  updateNodeVisibility();
  
  // Update the book card appearance - just update classes, don't re-render entire list
  const list = d3.select("#starting-books-list");
  list.selectAll("div.starting-book-item")
    .attr("class", d => {
      const isHidden = hiddenNodeIds.has(String(d.id));
      return "starting-book-item" + (isHidden ? " hidden" : "");
    });
  
  // Update node counts
  updateNodeCounts();
  
  console.log('Visibility toggled successfully');
}

function updateNodeVisibility() {
  if (!nodeGroup || !linkGroup) return;
  
  console.log('=== updateNodeVisibility ===');
  console.log('Hidden node IDs:', Array.from(hiddenNodeIds));
  console.log('Starting books:', startingBooks);
  console.log('Total nodes:', nodes.length);
  
  // Start fresh - only hide nodes that should be hidden based on CURRENT state
  const nodesToHide = new Set();
  
  // Add explicitly hidden starting books
  hiddenNodeIds.forEach(id => {
    const idStr = String(id);
    if (startingBooks.includes(idStr)) {
      nodesToHide.add(idStr);
      console.log('Adding hidden starting book to nodesToHide:', idStr);
    }
  });
  
  // For each HIDDEN starting book, find nodes that are ONLY reachable through it
  hiddenNodeIds.forEach(hiddenId => {
    const hiddenIdStr = String(hiddenId);
    const isStartingBook = startingBooks.includes(hiddenIdStr);
    
    if (!isStartingBook) return; // Only process starting books
    
    console.log(`Processing hidden starting book: ${hiddenIdStr}`);
    
    // Find all nodes directly referenced by this hidden book
    const directlyReferenced = new Set();
    links.forEach(link => {
      const sourceId = String(typeof link.source === 'object' ? link.source.id : link.source);
      const targetId = String(typeof link.target === 'object' ? link.target.id : link.target);
      
      if (sourceId === hiddenIdStr) {
        directlyReferenced.add(String(targetId));
      }
    });
    
    console.log(`  Directly referenced nodes:`, Array.from(directlyReferenced));
    
    // For each directly referenced node, check if it has a DIRECT connection to another visible starting book
    // If it does, keep it visible. Otherwise, hide it and its network.
    directlyReferenced.forEach(refNodeId => {
      const refNodeIdStr = String(refNodeId);
      
      // Don't hide other starting books
      if (startingBooks.includes(refNodeIdStr)) {
        return;
      }
      
      // Check if this node has a DIRECT link to any OTHER visible starting book
      let hasDirectLinkToOtherVisible = false;
      
      startingBooks.forEach(startId => {
        const startIdStr = String(startId);
        // Only check OTHER visible starting books (not the one we're hiding)
        if (startIdStr !== hiddenIdStr && !hiddenNodeIds.has(startIdStr)) {
          // Check for direct link (not through intermediate nodes)
          links.forEach(link => {
            const sourceId = String(typeof link.source === 'object' ? link.source.id : link.source);
            const targetId = String(typeof link.target === 'object' ? link.target.id : link.target);
            
            if ((sourceId === startIdStr && targetId === refNodeIdStr) ||
                (targetId === startIdStr && sourceId === refNodeIdStr)) {
              hasDirectLinkToOtherVisible = true;
            }
          });
        }
      });
      
      // If NOT directly connected to another visible starting book, hide it and its network
      if (!hasDirectLinkToOtherVisible) {
        console.log(`  Hiding node ${refNodeIdStr} (not directly connected to other visible starting books)`);
        nodesToHide.add(refNodeIdStr);
        
        // Also hide all nodes in this node's network (BFS from this node)
        const queue = [refNodeIdStr];
        const visited = new Set([refNodeIdStr]);
        const networkNodes = [refNodeIdStr];
        
        while (queue.length > 0) {
          const currentId = queue.shift();
          
          links.forEach(link => {
            const sourceId = String(typeof link.source === 'object' ? link.source.id : link.source);
            const targetId = String(typeof link.target === 'object' ? link.target.id : link.target);
            
            let nextId = null;
            if (sourceId === currentId) nextId = String(targetId);
            if (targetId === currentId) nextId = String(sourceId);
            
            if (nextId && !visited.has(nextId)) {
              // Don't hide starting books
              if (!startingBooks.includes(nextId)) {
                // Check if this node has a direct link to another visible starting book
                let hasDirectLink = false;
                startingBooks.forEach(otherStartId => {
                  const otherStartIdStr = String(otherStartId);
                  if (otherStartIdStr !== hiddenIdStr && !hiddenNodeIds.has(otherStartIdStr)) {
                    links.forEach(checkLink => {
                      const checkSource = String(typeof checkLink.source === 'object' ? checkLink.source.id : checkLink.source);
                      const checkTarget = String(typeof checkLink.target === 'object' ? checkLink.target.id : checkLink.target);
                      if ((checkSource === otherStartIdStr && checkTarget === nextId) ||
                          (checkTarget === otherStartIdStr && checkSource === nextId)) {
                        hasDirectLink = true;
                      }
                    });
                  }
                });
                
                if (!hasDirectLink) {
                  nodesToHide.add(nextId);
                  visited.add(nextId);
                  queue.push(nextId);
                  networkNodes.push(nextId);
                }
              }
            }
          });
        }
        console.log(`  Also hiding network nodes:`, networkNodes);
      } else {
        console.log(`  Keeping node ${refNodeIdStr} (directly connected to other visible starting book)`);
      }
    });
  });
  
  console.log('Final nodesToHide:', Array.from(nodesToHide));
  console.log('Visible starting books:', startingBooks.filter(id => !hiddenNodeIds.has(String(id))));
  
  // Apply visibility with fast Processing-style animations - random timing for "spring alive" effect
  const nodeSelection = nodeGroup.selectAll("circle");
  
  // Collect nodes that need to be shown (for random animation)
  const nodesToShow = [];
  const nodesToHideAnimated = [];
  
  nodeSelection.each(function(d) {
    const nodeId = String(d.id);
    const element = d3.select(this);
    const shouldHide = d.isStarting 
      ? hiddenNodeIds.has(nodeId)
      : nodesToHide.has(nodeId);
    
    const wasVisible = parseFloat(element.style("opacity")) > 0;
    const isVisible = !shouldHide;
    
    if (wasVisible && !isVisible) {
      // Mark for hiding animation
      nodesToHideAnimated.push({ element, node: d });
    } else if (!wasVisible && isVisible) {
      // Mark for showing animation (only non-starting nodes get random timing)
      if (!d.isStarting) {
        nodesToShow.push({ element, node: d });
      } else {
        // Starting nodes show immediately
        element
          .style("opacity", 0)
          .attr("r", 0)
          .style("pointer-events", "all")
          .transition()
          .duration(350)
          .ease(d3.easeBackOut.overshoot(1.2))
          .attr("r", d.size)
          .style("opacity", 1);
      }
    } else if (isVisible) {
      // Already visible, ensure it stays visible
      element.style("opacity", 1).style("pointer-events", "all");
    } else {
      // Already hidden, ensure it stays hidden
      element.style("opacity", 0).style("pointer-events", "none");
    }
  });
  
  // Animate hiding nodes (fast, no random delay)
  nodesToHideAnimated.forEach(({ element, node }) => {
    element.transition()
      .duration(300)
      .ease(d3.easeQuadIn)
      .attr("r", 0)
      .style("opacity", 0)
      .on("end", function() {
        element.style("pointer-events", "none");
      });
  });
  
  // Animate showing nodes with RANDOM delays for "spring alive" effect
  nodesToShow.forEach(({ element, node }) => {
    const randomDelay = Math.random() * 400; // Random delay 0-400ms
    element
      .style("opacity", 0)
      .attr("r", 0)
      .style("pointer-events", "all")
      .transition()
      .delay(randomDelay)
      .duration(350)
      .ease(d3.easeBackOut.overshoot(1.2))
      .attr("r", node.size)
      .style("opacity", 1);
  });
  
  // Hide/show links with fast animation
  linkGroup.selectAll("line")
    .transition()
    .duration(300)
    .style("opacity", d => {
      const sourceId = String(typeof d.source === 'object' ? d.source.id : d.source);
      const targetId = String(typeof d.target === 'object' ? d.target.id : d.target);
      
      // Hide link if either node is hidden
      const sourceHidden = nodesToHide.has(sourceId) || (nodes.find(n => String(n.id) === sourceId)?.isStarting && hiddenNodeIds.has(sourceId));
      const targetHidden = nodesToHide.has(targetId) || (nodes.find(n => String(n.id) === targetId)?.isStarting && hiddenNodeIds.has(targetId));
      
      return (sourceHidden || targetHidden) ? 0 : 0.6; // Higher opacity for WCAG
    })
    .style("pointer-events", d => {
      const sourceId = String(typeof d.source === 'object' ? d.source.id : d.source);
      const targetId = String(typeof d.target === 'object' ? d.target.id : d.target);
      const sourceHidden = nodesToHide.has(sourceId) || (nodes.find(n => String(n.id) === sourceId)?.isStarting && hiddenNodeIds.has(sourceId));
      const targetHidden = nodesToHide.has(targetId) || (nodes.find(n => String(n.id) === targetId)?.isStarting && hiddenNodeIds.has(targetId));
      return (sourceHidden || targetHidden) ? "none" : "all";
    });
  
  console.log('Visibility update complete');
}


// Stats function removed - no longer displaying stats panel

function fetchDataAndRenderGraph() {
  console.log('Fetching data...');
  
  // Detect if we're in static mode (GitHub Pages) or Flask mode
  // Static mode: files are in same directory or docs/ directory
  // Flask mode: API endpoints are available
  const isStaticMode = window.location.pathname.includes('/docs/') || 
                       window.location.hostname.includes('github.io') ||
                       window.location.protocol === 'file:';
  
  let booksPromise, referencesPromise;
  
  if (isStaticMode) {
    // Static mode: load from JSON files
    console.log('Running in static mode, loading from JSON files...');
    // Determine base path based on current location
    let basePath = '';
    const pathname = window.location.pathname;
    
    if (pathname.includes('/docs/')) {
      // We're viewing from /docs/ folder directly (local file or docs subdirectory)
      basePath = '.';
    } else if (window.location.hostname.includes('github.io')) {
      // GitHub Pages - files are at root of repo, so use current path's base
      // e.g., https://user.github.io/repo/ -> basePath = '/repo'
      // e.g., https://user.github.io/repo/index.html -> basePath = '/repo'
      const pathParts = pathname.split('/').filter(p => p && p !== 'index.html');
      if (pathParts.length > 0) {
        basePath = '/' + pathParts[0];
      }
    } else {
      // Local file or other static hosting - use relative path
      basePath = '.';
    }
    
    console.log('Static mode base path:', basePath);
    booksPromise = fetch(`${basePath}/books.json`).then(res => {
      if (!res.ok) throw new Error(`Failed to load books.json: ${res.status}`);
      return res.json();
    });
    referencesPromise = fetch(`${basePath}/references.json`).then(res => {
      if (!res.ok) throw new Error(`Failed to load references.json: ${res.status}`);
      return res.json();
    });
  } else {
    // Flask mode: use API endpoints
    console.log('Running in Flask mode, loading from API...');
    booksPromise = fetch('/api/books').then(res => res.json());
    referencesPromise = fetch('/api/references').then(res => res.json());
  }
  
  Promise.all([booksPromise, referencesPromise])
    .then(([books, references]) => {
      console.log('Data fetched:', books.length, 'books,', references.length, 'references');
      // Store in global variables for use in other functions
      allBooks = books;
      allReferences = references;
      renderGraph(books, references);
    })
    .catch(error => {
      console.error('Error fetching data:', error);
      const container = document.getElementById('graph-container');
      if (container) {
        container.innerHTML = 
          '<div style="color: #ffd700; padding: 20px; text-align: center;">Error loading data. Please check the console for details.</div>';
      }
    });
}
    
    // Reset view button
document.addEventListener('DOMContentLoaded', function() {
  // Check if D3 is loaded
  if (typeof d3 === 'undefined') {
    console.error('D3.js failed to load! Check the CDN link.');
    document.getElementById('graph-container').innerHTML = 
      '<div style="color: #ffd700; padding: 20px; text-align: center;">Error: D3.js library failed to load. Please refresh the page.</div>';
    return;
  }
  
  console.log('D3.js loaded successfully, version:', d3.version);
  
  const resetBtn = document.getElementById('reset-view');
  if (resetBtn) {
    resetBtn.onclick = function() {
      deselectNode();
      if (svg && zoom) {
        svg.transition()
          .duration(500)
          .call(zoom.transform, d3.zoomIdentity);
      }
    };
  }
  
  // Remove toggle labels button (not needed)
  const toggleBtn = document.getElementById('toggle-labels');
  if (toggleBtn) {
    toggleBtn.style.display = 'none';
  }
  
  // Small delay to ensure DOM is ready
  setTimeout(() => {
fetchDataAndRenderGraph(); 
  }, 100);
});
