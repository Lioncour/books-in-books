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
  
  // Create SVG
  svg = container.append("svg")
    .attr("width", width)
    .attr("height", height)
    .style("background", "transparent")
    .style("display", "block");
  
  console.log('SVG created:', width, 'x', height);
  
  // Add defs for gradients and filters
  const defs = svg.append("defs");
  
  // Gradient for gold nodes
  const goldGradient = defs.append("linearGradient")
    .attr("id", "goldGradient")
    .attr("x1", "0%").attr("y1", "0%")
    .attr("x2", "100%").attr("y2", "100%");
  goldGradient.append("stop").attr("offset", "0%").attr("stop-color", "#ffd700");
  goldGradient.append("stop").attr("offset", "100%").attr("stop-color", "#ffed4e");
  
  // Gradient for blue nodes
  const blueGradient = defs.append("linearGradient")
    .attr("id", "blueGradient")
    .attr("x1", "0%").attr("y1", "0%")
    .attr("x2", "100%").attr("y2", "100%");
  blueGradient.append("stop").attr("offset", "0%").attr("stop-color", "#7f9cf5");
  blueGradient.append("stop").attr("offset", "100%").attr("stop-color", "#a3bffa");
  
  // Glow filter for nodes
  const glowFilter = defs.append("filter")
    .attr("id", "glow");
  glowFilter.append("feGaussianBlur")
    .attr("stdDeviation", "3")
    .attr("result", "coloredBlur");
  const feMerge = glowFilter.append("feMerge");
  feMerge.append("feMergeNode").attr("in", "coloredBlur");
  feMerge.append("feMergeNode").attr("in", "SourceGraphic");
  
  // Shadow filter
  const shadowFilter = defs.append("filter")
    .attr("id", "shadow");
  shadowFilter.append("feGaussianBlur")
    .attr("stdDeviation", "2")
    .attr("result", "coloredBlur");
  const feMergeShadow = shadowFilter.append("feMerge");
  feMergeShadow.append("feMergeNode").attr("in", "SourceGraphic");
  feMergeShadow.append("feMergeNode").attr("in", "coloredBlur");
  
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
  startingBooks = books.filter(b => b.is_starting).map(b => String(b.id));
  
  console.log(`Starting books found: ${startingBooks.length}`, startingBooks);
  
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
  
  // Remap references
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
  
  // Filter to only show nodes connected to starting books (directly or indirectly)
  const connectedBookIds = new Set(startingBooks);
  let changed = true;
  let iterations = 0;
  const maxIterations = 100; // Safety limit
  
  // Keep expanding until no new connections found (finds all nodes reachable from starting books)
  while (changed && iterations < maxIterations) {
    changed = false;
    references.forEach(ref => {
      const sourceId = String(ref.source);
      const targetId = String(ref.target);
      if (connectedBookIds.has(sourceId) && !connectedBookIds.has(targetId)) {
        connectedBookIds.add(targetId);
        changed = true;
      }
      if (connectedBookIds.has(targetId) && !connectedBookIds.has(sourceId)) {
        connectedBookIds.add(sourceId);
        changed = true;
      }
    });
    iterations++;
  }
  
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
    
    let size, color, gradient;
    if (isStarting) {
      size = Math.max(20, Math.min(35, 20 + connections * 2));
      color = "#ffd700";
      gradient = "url(#goldGradient)";
    } else if (connections > 0) {
      size = Math.max(10, Math.min(20, 10 + connections * 1.5));
      const intensity = Math.min(255, 150 + connections * 20);
      color = `rgb(100, 150, ${intensity})`;
      gradient = "url(#blueGradient)";
    } else {
      size = 8;
      color = "#666666";
      gradient = color;
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
      gradient: gradient,
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
  
  // Draw links - D3 will convert source/target IDs to node objects
  console.log('Drawing', links.length, 'links');
  const link = linkGroup.selectAll("line")
    .data(links)
    .enter().append("line")
    .attr("stroke", "#888888")
    .attr("stroke-opacity", 0.6)
    .attr("stroke-width", 2)
    .style("filter", "url(#shadow)")
    .style("stroke-linecap", "round");
  
  console.log('Links drawn:', link.size());
  
  // Draw nodes
  console.log('Drawing', nodes.length, 'nodes');
  const node = nodeGroup.selectAll("circle")
    .data(nodes)
    .enter().append("circle")
    .attr("r", d => d.size)
    .attr("fill", d => d.gradient)
    .style("cursor", "pointer")
    .style("filter", d => d.isStarting ? "url(#glow)" : "url(#shadow)")
    .on("mouseover", function(event, d) {
      showTooltip(event, d);
      d3.select(this)
        .transition()
        .duration(200)
        .attr("r", d.size * 1.2)
        .style("filter", d.isStarting ? "url(#glow)" : "url(#shadow)");
    })
    .on("mouseout", function(event, d) {
      hideTooltip();
      d3.select(this)
        .transition()
        .duration(200)
        .attr("r", d.size)
        .style("filter", d.isStarting ? "url(#glow)" : "url(#shadow)");
    })
  .on("click", function(event, d) {
    event.stopPropagation();
    selectNode(d);
  });
  
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
  function dragstarted(event, d) {
    if (!event.active) simulation.alphaTarget(0.3).restart();
    d.fx = d.x;
    d.fy = d.y;
  }
  
  function dragged(event, d) {
    // Only allow dragging starting books
    if (d.isStarting) {
      d.fx = event.x;
      d.fy = event.y;
    }
  }
  
  function dragended(event, d) {
    if (!event.active) simulation.alphaTarget(0);
    d.fx = null;
    d.fy = null;
  }
  
  return d3.drag()
    .on("start", dragstarted)
    .on("drag", dragged)
    .on("end", dragended);
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
  
  // Apply visibility - starting books are always visible if not in hiddenNodeIds
  nodeGroup.selectAll("circle")
    .style("opacity", d => {
      const nodeId = String(d.id);
      // Starting books: visible if not in hiddenNodeIds
      if (d.isStarting) {
        return hiddenNodeIds.has(nodeId) ? 0 : 1;
      }
      // Other nodes: visible if not in nodesToHide
      return nodesToHide.has(nodeId) ? 0 : 1;
    })
    .style("pointer-events", d => {
      const nodeId = String(d.id);
      if (d.isStarting) {
        return hiddenNodeIds.has(nodeId) ? "none" : "all";
      }
      return nodesToHide.has(nodeId) ? "none" : "all";
    });
  
  // Hide/show links
  linkGroup.selectAll("line")
    .style("opacity", d => {
      const sourceId = String(typeof d.source === 'object' ? d.source.id : d.source);
      const targetId = String(typeof d.target === 'object' ? d.target.id : d.target);
      
      // Hide link if either node is hidden
      const sourceHidden = nodesToHide.has(sourceId) || (nodes.find(n => String(n.id) === sourceId)?.isStarting && hiddenNodeIds.has(sourceId));
      const targetHidden = nodesToHide.has(targetId) || (nodes.find(n => String(n.id) === targetId)?.isStarting && hiddenNodeIds.has(targetId));
      
      return (sourceHidden || targetHidden) ? 0 : 0.6;
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
  Promise.all([
    fetch('/api/books').then(res => res.json()),
    fetch('/api/references').then(res => res.json())
  ]).then(([books, references]) => {
    console.log('Data fetched:', books.length, 'books,', references.length, 'references');
    // Store in global variables for use in other functions
    allBooks = books;
    allReferences = references;
    renderGraph(books, references);
  }).catch(error => {
    console.error('Error fetching data:', error);
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
